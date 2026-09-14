#!/usr/bin/env python3
"""Preserve a visual-intake session for a new run; never promote model readings."""

import argparse
import hashlib
import io
import json
import os
import sqlite3
from pathlib import Path

import pikepdf
from ingest_pages import sha256
from PIL import Image
from run_workspace import require_within_run_root
from visual_ingestion import BOUNDARY, IngestionStore, digest, encoded

SCHEMA = "visual_intake_source_package_v1"
MAX_DERIVATIVE_PIXELS = 50_000_000
MAX_FILE_BYTES = 250_000_000


def write_new(path, data):
    """Exclusive private files; an interrupted package is retained, never replaced."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def rgb_pixels(data):
    """Explicit display conversion, without EXIF rotation, scaling or OCR."""
    with Image.open(io.BytesIO(data)) as source:
        rgba = source.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(background, rgba).convert("RGB")


def source_pdf(pages, destination):
    """Losslessly encode display pixels beside, never instead of, original bytes."""
    dimensions = []
    for _, data in pages:
        with Image.open(io.BytesIO(data)) as source:
            dimensions.append((source.width, source.height))
    if sum(width * height for width, height in dimensions) > MAX_DERIVATIVE_PIXELS:
        raise ValueError("derivative_pixel_budget_exceeded")
    mapping = []
    with pikepdf.Pdf.new() as pdf:
        for (receipt, data), (width, height) in zip(pages, dimensions, strict=True):
            pixels = rgb_pixels(data)
            page_width, page_height = width * 72 / 300, height * 72 / 300
            page = pdf.add_blank_page(page_size=(page_width, page_height))
            stream = pdf.make_stream(pixels.tobytes())
            stream.Type = pikepdf.Name.XObject
            stream.Subtype = pikepdf.Name.Image
            stream.Width, stream.Height = width, height
            stream.ColorSpace = pikepdf.Name.DeviceRGB
            stream.BitsPerComponent = 8
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=stream))
            page.Contents = pdf.make_stream(
                f"q {page_width} 0 0 {page_height} 0 0 cm /Im0 Do Q\n".encode()
            )
            mapping.append(
                {
                    "source_id": receipt["source_id"],
                    "source_sha256": receipt["source_sha256"],
                    "page_number": receipt["page_number"],
                    "intake_page_id": f"{destination.stem}__p{receipt['page_number']:04d}",
                    "width": width,
                    "height": height,
                    "rgb_pixel_sha256": hashlib.sha256(pixels.tobytes()).hexdigest(),
                }
            )
        # Opening exclusively also prevents PDF save from replacing a partial run.
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            pdf.save(output, deterministic_id=True)
    return mapping


def review_exception(session_id, reason, field, page_number=None):
    """Use the existing final-review exceptions contract, including unique targets."""
    return {
        "document_id": f"visual_intake:{session_id}",
        "page_id": f"source_page:{page_number}" if page_number is not None else "",
        "field": field,
        "reason": reason,
        "review_source": "visual_intake",
        "client_review_required": True,
        "disposition": "client_review_required",
    }


def export_session(store, owner, session_id, out_dir):
    """Snapshot first, preserve every entry, then prepare only a complete source set."""
    root = Path(out_dir).absolute()
    if root.resolve().is_relative_to(store.root.resolve()) or store.root.resolve().is_relative_to(
        root.resolve()
    ):
        raise ValueError("Export and intake journal directories must be separate")
    if os.environ.get("BUSINESS_DOC_RUN_ROOT"):
        require_within_run_root(os.environ["BUSINESS_DOC_RUN_ROOT"], root)
    status, rows = store.snapshot(owner, session_id)
    # Exclusive directory creation reserves the whole package against racing exporters.
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / "originals").mkdir(mode=0o700)
    entries, pages, exceptions = [], {}, []
    for index, row in enumerate(rows):
        entry = {key: value for key, value in row.items() if key != "data"}
        result = json.loads(row["result"])
        entry["data_file"] = None
        if row["kind"] == "page":
            relative = f"originals/{index:04d}.bin"
            write_new(root / relative, row["data"])
            entry["data_file"] = relative
            if result["status"] == "retained":
                pages[result["page_number"]] = (result, row["data"])
            else:
                exceptions.append(
                    {
                        **review_exception(
                            session_id,
                            "visual_intake_rejected_upload",
                            row["id"],
                            result["page_number"],
                        ),
                        "findings": result["findings"],
                    }
                )
        elif row["kind"] == "proposal":
            exceptions.append(
                {
                    **review_exception(
                        session_id, "visual_intake_proposal_requires_review", row["id"]
                    ),
                    "proposal_status": result["status"],
                    "findings": result["findings"],
                    "proposal_json_pointer": f"/entries/{index}/payload",
                }
            )
        entries.append(entry)
    write_new(
        root / "journal.json",
        encoded({"tenant": store.tenant, "owner": owner, "status": status, "entries": entries}),
    )
    for number in range(1, status["expected_pages"] + 1):
        if number not in pages:
            exceptions.append(
                review_exception(session_id, "visual_intake_missing_page", "source", number)
            )
    # This remains review-bearing even without a model proposal or upload error.
    exceptions.append(
        review_exception(session_id, "visual_intake_source_verification_required", "source_set")
    )
    prepared, mapping = False, []
    pdf_name = (
        "visual_" + digest({"tenant": store.tenant, "owner": owner, "session": session_id}) + ".pdf"
    )
    if len(pages) == status["expected_pages"]:
        try:
            mapping = source_pdf([pages[key] for key in sorted(pages)], root / pdf_name)
            prepared = True
        except (OSError, ValueError, pikepdf.PdfError) as exc:
            exceptions.append(
                {
                    **review_exception(
                        session_id, "visual_intake_source_preparation_failed", "source_set"
                    ),
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                }
            )
    write_new(root / "exceptions.json", encoded({"exceptions": exceptions, **BOUNDARY}))
    files = [
        {"path": str(path.relative_to(root)), "sha256": sha256(path), "bytes": path.stat().st_size}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    manifest = {
        "schema_version": SCHEMA,
        "session_id": session_id,
        "source_set_sha256": status["source_set_sha256"],
        "status": "source_prepared_review_required" if prepared else "blocked_source_preparation",
        "entries": len(entries),
        "expected_pages": status["expected_pages"],
        "retained_pages": len(pages),
        "proposal_count": status["proposal_count"],
        "files": files,
        "source_pdf": pdf_name if prepared else None,
        "page_map": mapping,
        "transformation": {
            "pixel_resize": False,
            "exif_rotation_applied": False,
            "color": "Pillow RGBA to RGB; alpha composited over white; no ICC color correction",
            "pdf_layout_dpi": 300,
            "original_physical_scale_known": False,
            "compression": "lossless PDF image streams",
            "ocr_or_text_layer_added": False,
        },
        "independent_consensus_input": False,
        "exceptions": "exceptions.json",
        **BOUNDARY,
    }
    manifest["package_sha256"] = digest(manifest)
    write_new(root / "manifest.json", encoded(manifest))
    checksum = sha256(root / "manifest.json")
    verify_package(root, checksum)
    return {
        "status": manifest["status"],
        "manifest_sha256": checksum,
        "entries": len(entries),
        **BOUNDARY,
    }


def verify_package(directory, manifest_sha256):
    """Pin the retained manifest, then verify all bytes and journal entry integrity."""
    root = Path(directory)
    manifest_path = root / "manifest.json"
    if root.is_symlink() or manifest_path.is_symlink():
        raise ValueError("Package paths must not be symlinks")
    if manifest_path.stat().st_size > 1_000_000 or sha256(manifest_path) != manifest_sha256:
        raise ValueError("Package manifest checksum or size mismatch")
    manifest = json.loads(manifest_path.read_bytes())
    package_hash = manifest.pop("package_sha256")
    if manifest["schema_version"] != SCHEMA or digest(manifest) != package_hash:
        raise ValueError("Package contract or content checksum mismatch")
    paths = set()
    for item in manifest["files"]:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts or item["path"] in paths:
            raise ValueError("Unsafe or duplicate package path")
        path = root / relative
        if any(
            root.joinpath(*relative.parts[:index]).is_symlink()
            for index in range(1, len(relative.parts) + 1)
        ):
            raise ValueError("Package paths must not be symlinks")
        if (
            not path.is_file()
            or path.stat().st_size != item["bytes"]
            or item["bytes"] > MAX_FILE_BYTES
        ):
            raise ValueError("Package file size or type mismatch")
        if sha256(path) != item["sha256"]:
            raise ValueError("Package file checksum mismatch")
        paths.add(item["path"])
    members = list(root.rglob("*"))
    if any(path.is_symlink() for path in members):
        raise ValueError("Package paths must not be symlinks")
    actual = {str(path.relative_to(root)) for path in members if not path.is_dir()}
    if actual != paths | {"manifest.json"}:
        raise ValueError("Package contains missing or unlisted files")
    journal = json.loads((root / "journal.json").read_bytes())
    if len(journal["entries"]) != manifest["entries"]:
        raise ValueError("Package entry count mismatch")
    for entry in journal["entries"]:
        relative = entry.pop("data_file")
        if relative is not None and relative not in paths:
            raise ValueError("Unbound journal data file")
        entry["data"] = (root / relative).read_bytes() if relative is not None else b""
        IngestionStore._read(entry)
    return {"verified": True, "status": manifest["status"], "files": len(paths), **BOUNDARY}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="retain one session and prepare source-only PDF")
    export.add_argument("directory", help="existing private intake directory, opened read-only")
    export.add_argument("--tenant", required=True, help="exact tenant bound to the journal")
    export.add_argument(
        "--owner", required=True, help="exact session owner; trusted local operator only"
    )
    export.add_argument("--session-id", required=True, help="session ID from the intake receipt")
    export.add_argument(
        "--out-dir", required=True, help="new private source-package directory inside the run"
    )
    verify = commands.add_parser(
        "verify", help="verify a package against the retained manifest checksum"
    )
    verify.add_argument("directory", help="retained source-package directory; no writes")
    verify.add_argument(
        "--manifest-sha256",
        required=True,
        help="exact export receipt checksum, retained separately",
    )
    args = parser.parse_args()
    try:
        if args.command == "export":
            store = IngestionStore(args.directory, args.tenant, read_only=True)
            result = export_session(store, args.owner, args.session_id, args.out_dir)
        else:
            result = verify_package(args.directory, args.manifest_sha256)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.exit(1, f"Visual intake handoff refused: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    if result["status"] == "blocked_source_preparation":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

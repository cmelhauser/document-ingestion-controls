"""Source-only handoff: exact originals, every rejection, and no control clearance."""

import base64
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pikepdf
import pytest
import visual_ingestion_export as handoff
from client_review.queue import review_bearing, review_items
from ingest_pages import sha256, split_pdf
from PIL import Image
from test_visual_ingestion import picture, proposal, session, upload
from visual_ingestion import IngestionStore, digest, encoded


@pytest.fixture
def ready(tmp_path):
    store = IngestionStore(tmp_path / "intake", "tenant-a")
    sid = session(store)
    upload(store, sid)
    return store, sid, tmp_path / "package"


def read(path):
    return json.loads(path.read_bytes())


def repin(root, manifest):
    manifest.pop("package_sha256", None)
    manifest["package_sha256"] = digest(manifest)
    (root / "manifest.json").write_bytes(encoded(manifest))
    return sha256(root / "manifest.json")


def test_source_handoff_roundtrip_and_pipeline_contract(ready):
    store, sid, root = ready
    bad = store.upload_page("user-a", sid, "bad", 1, "image/png", "YmFk")
    invalid = store.submit("user-a", sid, "rejected", {"approved": True})
    valid = store.submit("user-a", sid, "valid", proposal(store, sid))
    original_db_hash = sha256(store.path)
    result = handoff.export_session(
        IngestionStore(store.root, "tenant-a", read_only=True), "user-a", sid, root
    )
    assert result["status"] == "source_prepared_review_required"
    assert result["canonical_write_permitted"] is False
    assert sha256(store.path) == original_db_hash
    assert handoff.verify_package(root, result["manifest_sha256"])["verified"]
    journal = read(root / "journal.json")
    assert journal["status"]["proposal_count"] == 2
    assert {row["id"] for row in journal["entries"]} >= {
        bad["source_id"],
        invalid["proposal_id"],
        valid["proposal_id"],
    }
    rows = journal["entries"]
    assert (root / rows[1]["data_file"]).read_bytes() == base64.b64decode(picture())
    assert (root / rows[2]["data_file"]).read_bytes() == b"bad"
    assert json.loads(rows[3]["payload"]) == {"approved": True}
    review = read(root / "exceptions.json")
    assert review_bearing(review) and len(review_items(review)) == 4
    manifest = read(root / "manifest.json")
    assert manifest["page_map"][0]["source_sha256"] == sha256(root / rows[1]["data_file"])
    source = root / manifest["source_pdf"]
    with pikepdf.open(source) as pdf:
        assert len(pdf.pages) == 1
        pixels = pikepdf.PdfImage(pdf.pages[0].Resources.XObject.Im0).as_pil_image()
        assert pixels.size == (40, 30) and pixels.getpixel((0, 0)) == (255, 255, 255)
    # Actual producer creates the manifest; the bridge never fabricates one.
    intake = root.parent / "pipeline-intake"
    intake.mkdir()
    pipeline, exceptions = split_pdf(source, intake)
    assert len(pipeline["pages"]) == 1 and len(exceptions["exceptions"]) == 1
    assert pipeline["pages"][0]["source_sha256"] == sha256(source)
    assert pipeline["pages"][0]["page_id"] == manifest["page_map"][0]["intake_page_id"]
    assert pipeline["pages"][0]["document_id"] is None
    assert not root.stat().st_mode & 0o077
    assert all(not path.stat().st_mode & 0o077 for path in root.rglob("*") if path.is_file())


def test_page_order_transparency_and_jpeg(tmp_path):
    store = IngestionStore(tmp_path / "intake", "tenant")
    sid = session(store, pages=2)
    raw = io.BytesIO()
    Image.new("RGB", (60, 40), "red").save(raw, "JPEG")
    store.upload_page(
        "user-a", sid, "two", 2, "image/jpeg", base64.b64encode(raw.getvalue()).decode()
    )
    raw = io.BytesIO()
    source = Image.new("RGBA", (40, 60), (0, 0, 255, 0))
    source.putpixel((2, 3), (0, 255, 0, 255))
    source.save(raw, "PNG")
    store.upload_page(
        "user-a", sid, "one", 1, "image/png", base64.b64encode(raw.getvalue()).decode()
    )
    root = tmp_path / "package"
    handoff.export_session(store, "user-a", sid, root)
    manifest = read(root / "manifest.json")
    assert [row["page_number"] for row in manifest["page_map"]] == [1, 2]
    assert not manifest["transformation"]["exif_rotation_applied"]
    with pikepdf.open(root / manifest["source_pdf"]) as pdf:
        image = pikepdf.PdfImage(pdf.pages[0].Resources.XObject.Im0).as_pil_image()
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert image.getpixel((2, 3)) == (0, 255, 0)
        assert pikepdf.PdfImage(pdf.pages[1].Resources.XObject.Im0).as_pil_image().size == (60, 40)


def test_incomplete_empty_and_failed_preparation_retained(tmp_path, ready, monkeypatch):
    store = IngestionStore(tmp_path / "empty-intake", "tenant")
    sid = session(store, pages=2)
    root = tmp_path / "empty-package"
    result = handoff.export_session(store, "user-a", sid, root)
    assert result["status"] == "blocked_source_preparation"
    assert not list(root.glob("*.pdf"))
    assert len(read(root / "exceptions.json")["exceptions"]) == 3
    assert handoff.verify_package(root, result["manifest_sha256"])["verified"]
    store, sid, root = ready
    monkeypatch.setattr(handoff, "MAX_DERIVATIVE_PIXELS", 1)
    result = handoff.export_session(store, "user-a", sid, root)
    assert result["status"] == "blocked_source_preparation"
    assert (
        read(root / "exceptions.json")["exceptions"][-1]["detail"]
        == "derivative_pixel_budget_exceeded"
    )
    assert len(list((root / "originals").iterdir())) == 1


def test_readonly_and_scope_fail_before_output(ready, tmp_path, monkeypatch):
    store, sid, root = ready
    with pytest.raises(FileNotFoundError):
        IngestionStore(tmp_path / "missing", "tenant", read_only=True)
    assert not (tmp_path / "missing").exists()
    read_only = IngestionStore(store.root, "tenant-a", read_only=True)
    with pytest.raises(ValueError, match="read-only"):
        read_only.create_session("user-a", "new", 1)
    with pytest.raises(ValueError, match="mismatch"):
        IngestionStore(store.root, "wrong", read_only=True)
    with pytest.raises(ValueError, match="not found"):
        handoff.export_session(read_only, "other", sid, root)
    assert not root.exists()
    for destination in (store.root, store.root / "export", store.root.parent):
        with pytest.raises(ValueError, match="separate"):
            handoff.export_session(store, "user-a", sid, destination)
    monkeypatch.setenv("BUSINESS_DOC_RUN_ROOT", str(tmp_path / "run"))
    with pytest.raises(ValueError, match="escapes"):
        handoff.export_session(store, "user-a", sid, root)
    result = handoff.export_session(store, "user-a", sid, tmp_path / "run" / "source-package")
    assert result["status"] == "source_prepared_review_required"


def test_no_clobber_concurrent_and_partial_output(ready, monkeypatch):
    store, sid, root = ready

    def run():
        try:
            return handoff.export_session(store, "user-a", sid, root)
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(result is not None for result in results) == 1
    retained = sha256(root / "manifest.json")
    with pytest.raises(FileExistsError):
        handoff.write_new(root / "manifest.json", b"replace")
    assert sha256(root / "manifest.json") == retained

    def failed_pdf(pages, destination):
        destination.write_bytes(b"partial")
        raise OSError("fictional disk failure")

    monkeypatch.setattr(handoff, "source_pdf", failed_pdf)
    partial = root.parent / "partial"
    result = handoff.export_session(store, "user-a", sid, partial)
    assert result["status"] == "blocked_source_preparation"
    assert read(partial / "manifest.json")["source_pdf"] is None
    assert next(partial.glob("*.pdf")).read_bytes() == b"partial"


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("manifest", "manifest checksum"),
        ("contract", "contract"),
        ("duplicate", "duplicate"),
        ("escape", "Unsafe"),
        ("absolute", "Unsafe"),
        ("size", "size"),
        ("checksum", "file checksum"),
        ("missing", "size"),
        ("unlisted", "unlisted"),
        ("entry_count", "entry count"),
        ("entry_data", "Unbound"),
        ("entry_integrity", "integrity"),
        ("symlink", "symlinks"),
        ("extra_symlink", "symlinks"),
    ],
)
def test_verifier_fails_closed(ready, mutation, message):
    store, sid, root = ready
    receipt = handoff.export_session(store, "user-a", sid, root)
    manifest = read(root / "manifest.json")
    if mutation == "manifest":
        (root / "manifest.json").write_bytes(b"{}")
        checksum = receipt["manifest_sha256"]
    else:
        if mutation == "contract":
            manifest["schema_version"] = "bad"
        elif mutation == "duplicate":
            manifest["files"].append(manifest["files"][0])
        elif mutation == "escape":
            manifest["files"][0]["path"] = "../escape"
        elif mutation == "absolute":
            manifest["files"][0]["path"] = "/etc/hosts"
        elif mutation == "size":
            manifest["files"][0]["bytes"] += 1
        elif mutation == "checksum":
            manifest["files"][0]["sha256"] = "bad"
        elif mutation == "missing":
            (root / manifest["files"][0]["path"]).unlink()
        elif mutation == "unlisted":
            (root / "extra").write_bytes(b"extra")
        elif mutation == "entry_count":
            manifest["entries"] += 1
        elif mutation in {"entry_data", "entry_integrity"}:
            journal = read(root / "journal.json")
            if mutation == "entry_data":
                journal["entries"][0]["data_file"] = "../bad"
            else:
                journal["entries"][0]["payload"] = "{}"
            (root / "journal.json").write_bytes(encoded(journal))
            item = next(item for item in manifest["files"] if item["path"] == "journal.json")
            item.update(
                sha256=sha256(root / "journal.json"), bytes=(root / "journal.json").stat().st_size
            )
        elif mutation == "symlink":
            path = root / manifest["files"][0]["path"]
            path.unlink()
            path.symlink_to(root / "manifest.json")
        elif mutation == "extra_symlink":
            (root / "extra").symlink_to(root / "originals", target_is_directory=True)
        checksum = repin(root, manifest)
    with pytest.raises(ValueError, match=message):
        handoff.verify_package(root, checksum)


def test_root_and_manifest_symlink_and_bound(ready, monkeypatch):
    store, sid, root = ready
    result = handoff.export_session(store, "user-a", sid, root)
    alias = root.parent / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        handoff.verify_package(alias, result["manifest_sha256"])
    monkeypatch.setattr(handoff, "MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="size"):
        handoff.verify_package(root, result["manifest_sha256"])


def test_cli_modes_and_failure(ready, monkeypatch, capsys):
    store, sid, root = ready
    argv = [
        "export",
        str(store.root),
        "--tenant",
        "tenant-a",
        "--owner",
        "user-a",
        "--session-id",
        sid,
        "--out-dir",
        str(root),
    ]
    monkeypatch.setattr("sys.argv", ["visual_ingestion_export.py", *argv])
    handoff.main()
    receipt = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        "sys.argv",
        [
            "visual_ingestion_export.py",
            "verify",
            str(root),
            "--manifest-sha256",
            receipt["manifest_sha256"],
        ],
    )
    handoff.main()
    assert json.loads(capsys.readouterr().out)["verified"]
    monkeypatch.setattr("sys.argv", ["visual_ingestion_export.py", *argv])
    with pytest.raises(SystemExit) as error:
        handoff.main()
    assert error.value.code == 1
    empty = store.create_session("user-a", "empty", 1)["session_id"]
    argv[argv.index(sid)] = empty
    argv[-1] = str(root.parent / "empty")
    monkeypatch.setattr("sys.argv", ["visual_ingestion_export.py", *argv])
    with pytest.raises(SystemExit) as error:
        handoff.main()
    assert error.value.code == 2


def test_database_corruption_no_export(ready):
    store, sid, root = ready
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER no_update")
        connection.execute("UPDATE entries SET integrity='invalid' WHERE kind='page'")
    with pytest.raises(ValueError, match="integrity"):
        handoff.export_session(store, "user-a", sid, root)
    assert not root.exists()


def test_later_snapshot_preserves_prior_and_session_page_identity(ready):
    store, sid, root = ready
    first = handoff.export_session(store, "user-a", sid, root)
    store.submit("user-a", sid, "later", proposal(store, sid))
    later = root.parent / "later"
    handoff.export_session(store, "user-a", sid, later)
    assert sha256(root / "manifest.json") == first["manifest_sha256"]
    assert read(root / "manifest.json")["proposal_count"] == 0
    assert read(later / "manifest.json")["proposal_count"] == 1
    name = read(root / "manifest.json")["source_pdf"]
    assert name == read(later / "manifest.json")["source_pdf"]
    assert sha256(root / name) == sha256(later / name)
    other_id = store.create_session("user-a", "another", 1)["session_id"]
    upload(store, other_id)
    other = root.parent / "another"
    handoff.export_session(store, "user-a", other_id, other)
    assert (
        read(other / "manifest.json")["page_map"][0]["intake_page_id"]
        != read(root / "manifest.json")["page_map"][0]["intake_page_id"]
    )

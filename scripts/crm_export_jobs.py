"""Bounded, checksummed CSV/XLSX export jobs for approved CRM snapshots."""

import csv
import hashlib
import json
import re
import secrets
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import xlsxwriter
from canonical_load import load_export, stable_checksum
from crm_import_package import create_package as create_common_import_package
from crm_service import MAX_PAGE_SIZE, export_summary, export_table, run_report
from csv_api_staging import create_package as create_staging_package
from csv_api_staging import load_plan, verify_plan
from review_export_link import validate_review_context

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
TABULAR_FORMATS = {"csv", "xlsx"}
PACKAGE_KIND_TO_ARTIFACT = {
    "staging": "crm_staging_package.zip",
    "common_import": "crm_common_import_package.zip",
}
MAX_XLSX_CELL_CHARACTERS = 32_767
MAX_XLSX_COLUMNS = 16_384
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _display(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    else:
        value = str(value)
    return f"'{value}" if value.startswith(FORMULA_PREFIXES) else value


def _columns(rows):
    return sorted({key for row in rows for key in row})


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_directory(root, artifact):
    """Write a stable ZIP archive containing every file beneath root."""
    with zipfile.ZipFile(artifact, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(Path(root).rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root))


def _public_manifest(manifest):
    """Return the owner-safe manifest view, flattening tabular summaries for compatibility."""
    visible = {
        key: value
        for key, value in manifest.items()
        if key not in {"download_token_sha256", "owner_sha256"}
    }
    tabular = visible.get("tabular_summary")
    if isinstance(tabular, dict):
        visible.setdefault("rows", tabular.get("rows"))
        visible.setdefault("columns", tabular.get("columns"))
        visible.setdefault("rows_sha256", tabular.get("rows_sha256"))
    return visible


def _table_rows(database, table, filters, max_rows, source_export_sha256):
    rows = []
    offset = 0
    while True:
        page = export_table(database, table, MAX_PAGE_SIZE, offset, filters)
        if page.get("source_export_sha256") != source_export_sha256:
            raise ValueError("CRM snapshot changed while the export job was being created")
        rows.extend(page["rows"])
        if len(rows) > max_rows:
            raise ValueError(f"export exceeds the configured {max_rows}-row limit")
        if page["next_offset"] is None:
            return rows
        offset = page["next_offset"]


def _write_csv(path, rows, columns):
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_display(row.get(column)) for column in columns])


def _write_xlsx(path, rows, columns, summary):
    if len(columns) > MAX_XLSX_COLUMNS:
        raise ValueError("XLSX export exceeds the Excel column limit; use csv")
    for row in rows:
        if any(len(_display(row.get(column))) > MAX_XLSX_CELL_CHARACTERS for column in columns):
            raise ValueError("XLSX export contains a cell above the Excel character limit; use csv")
    workbook = xlsxwriter.Workbook(path, {"constant_memory": True})
    try:
        header = workbook.add_format({"bold": True, "bg_color": "#D9E1F2"})
        summary_sheet = workbook.add_worksheet("Export Summary")
        for index, (key, value) in enumerate(summary.items()):
            summary_sheet.write_string(index, 0, key, header)
            summary_sheet.write_string(index, 1, _display(value))
        summary_sheet.set_column(0, 0, 28)
        summary_sheet.set_column(1, 1, 72)
        data = workbook.add_worksheet("CRM Data")
        for column_index, column in enumerate(columns):
            data.write_string(0, column_index, column, header)
        for row_index, row in enumerate(rows, 1):
            for column_index, column in enumerate(columns):
                data.write_string(row_index, column_index, _display(row.get(column)))
        if columns:
            data.autofilter(0, 0, len(rows), len(columns) - 1)
            data.freeze_panes(1, 0)
            data.set_column(0, len(columns) - 1, 18)
    finally:
        workbook.close()


class ExportJobs:
    """Create immutable export artifacts and validate short-lived downloads."""

    def __init__(
        self,
        database,
        root,
        base_url,
        ttl_seconds=900,
        max_rows=10_000,
        canonical_export=None,
        canonical_load_plan=None,
    ):
        self.database = str(database)
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        if not Path(database).is_file():
            raise ValueError("Retrieval database is not readable")
        parsed_base = urlparse(self.base_url)
        if (
            parsed_base.scheme != "https"
            or not parsed_base.netloc
            or parsed_base.path
            or parsed_base.params
            or parsed_base.query
            or parsed_base.fragment
            or parsed_base.username
            or parsed_base.password
        ):
            raise ValueError("download base URL must be an absolute HTTPS origin")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 60 <= ttl_seconds <= 3600
        ):
            raise ValueError("export TTL must be an integer from 60 through 3600 seconds")
        if (
            isinstance(max_rows, bool)
            or not isinstance(max_rows, int)
            or not 1 <= max_rows <= 100_000
        ):
            raise ValueError("maximum export rows must be an integer from 1 through 100000")
        self.ttl_seconds = ttl_seconds
        self.max_rows = max_rows
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.root.is_dir() or self.root.is_symlink():
            raise ValueError("export root must be a real directory")
        if stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise ValueError("export root must not grant group or other permissions")
        self._package_sources = self._package_sources_for(canonical_export, canonical_load_plan)

    def _package_sources_for(self, canonical_export, canonical_load_plan):
        """Validate optional package-generation inputs against this snapshot."""
        if canonical_export is None and canonical_load_plan is None:
            return None
        if canonical_export is None or canonical_load_plan is None:
            raise ValueError("canonical export and load plan must be supplied together")
        export_path = Path(canonical_export)
        plan_path = Path(canonical_load_plan)
        if not export_path.is_file() or not plan_path.is_file():
            raise ValueError("canonical export and load plan must be readable files")
        export = load_export(export_path)
        supplied = load_plan(plan_path)
        plan = verify_plan(export, supplied)
        snapshot = export_summary(self.database)
        if (
            export.get("batch_id") != snapshot["batch_id"]
            or plan["source_export_sha256"] != snapshot["source_export_sha256"]
        ):
            raise ValueError(
                "canonical export and load plan must match the served retrieval snapshot"
            )
        return {"canonical_export": str(export_path), "canonical_load_plan": str(plan_path)}

    def create(
        self,
        owner,
        *,
        table=None,
        report=None,
        package=None,
        format="xlsx",
        filters=None,
        parameters=None,
        review_context=None,
    ):
        """Queue one export or package job for an owner."""
        if not isinstance(owner, str) or not owner:
            raise ValueError("export owner must be a non-empty string")
        selected = [item is not None for item in (table, report, package)]
        if sum(selected) != 1:
            raise ValueError("supply exactly one of table, report, or package")
        filters = {} if filters is None else filters
        parameters = {} if parameters is None else parameters
        if not isinstance(filters, dict) or not isinstance(parameters, dict):
            raise ValueError("filters and parameters must be objects")
        if package is not None:
            if format not in {"xlsx", "zip"}:
                raise ValueError("package format must be zip")
            if format == "xlsx":
                format = "zip"
            if filters or parameters:
                raise ValueError("package exports do not accept filters or parameters")
            return self._create_package_job(owner, package, validate_review_context(review_context))
        if format not in TABULAR_FORMATS:
            raise ValueError("format must be csv or xlsx")
        snapshot = export_summary(self.database)
        if table is not None:
            rows = _table_rows(
                self.database,
                table,
                filters,
                self.max_rows,
                snapshot["source_export_sha256"],
            )
            subject = {"kind": "table", "name": table, "arguments": filters}
        else:
            result = run_report(self.database, report, parameters)
            if result.get("source_export_sha256") != snapshot["source_export_sha256"]:
                raise ValueError("CRM snapshot changed while the export job was being created")
            rows = result["rows"]
            if len(rows) > self.max_rows:
                raise ValueError(f"export exceeds the configured {self.max_rows}-row limit")
            subject = {"kind": "report", "name": report, "arguments": parameters}
        return self._create_tabular_job(owner, subject, format, rows, snapshot)

    def _create_tabular_job(self, owner, subject, format_name, rows, snapshot):
        """Create a downloadable CSV or XLSX export job."""
        columns = _columns(rows)
        tabular_summary = {
            "rows": len(rows),
            "columns": columns,
            "rows_sha256": stable_checksum(rows),
        }
        return self._create_job(
            owner,
            subject,
            format_name,
            "crm_export",
            snapshot["batch_id"],
            snapshot["source_export_sha256"],
            tabular_summary=tabular_summary,
            writer=lambda artifact, summary: (
                _write_csv(artifact, rows, columns)
                if format_name == "csv"
                else _write_xlsx(artifact, rows, columns, summary)
            ),
        )

    def _create_package_job(self, owner, package_kind, review_context):
        """Create a downloadable ZIP containing an existing no-send CRM package."""
        if package_kind not in PACKAGE_KIND_TO_ARTIFACT:
            raise ValueError("package must be staging or common_import")
        if self._package_sources is None:
            raise ValueError("package exports are not enabled for this deployment")
        batch = load_export(self._package_sources["canonical_export"])
        plan = load_plan(self._package_sources["canonical_load_plan"])
        verified_plan = verify_plan(batch, plan)
        package_root = Path(tempfile.mkdtemp(prefix=f".{package_kind}-package.", dir=self.root))
        package_dir = package_root / "content"
        try:
            if package_kind == "staging":
                package_summary = create_staging_package(
                    self._package_sources["canonical_export"],
                    self._package_sources["canonical_load_plan"],
                    package_dir,
                    review_context=review_context,
                )
            else:
                package_summary = create_common_import_package(
                    self._package_sources["canonical_export"],
                    self._package_sources["canonical_load_plan"],
                    package_dir,
                    review_context=review_context,
                )
            return self._create_job(
                owner,
                {
                    "kind": "package",
                    "name": package_kind,
                    "arguments": {"review_context": review_context} if review_context else {},
                },
                "zip",
                PACKAGE_KIND_TO_ARTIFACT[package_kind].removesuffix(".zip"),
                batch["batch_id"],
                verified_plan["source_export_sha256"],
                package_summary=package_summary,
                writer=lambda artifact, _summary: _zip_directory(package_dir, artifact),
            )
        finally:
            shutil.rmtree(package_root, ignore_errors=True)

    def _create_job(
        self,
        owner,
        subject,
        format_name,
        artifact_stem,
        batch_id,
        source_export_sha256,
        *,
        tabular_summary=None,
        package_summary=None,
        writer,
    ):
        """Create one immutable downloadable artifact manifest and file."""
        job_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(32)
        directory = self.root / job_id
        directory.mkdir(mode=0o700)
        try:
            artifact = directory / f"{artifact_stem}.{format_name}"
            created = int(time.time())
            summary = {
                "schema_version": "crm_downloadable_export_v2",
                "job_id": job_id,
                "subject": subject,
                "format": format_name,
                "batch_id": batch_id,
                "source_export_sha256": source_export_sha256,
                "created_at_epoch": created,
                "expires_at_epoch": created + self.ttl_seconds,
                "tabular_summary": tabular_summary,
                "package_summary": package_summary,
            }
            writer(artifact, summary)
            manifest = {
                **summary,
                "status": "completed",
                "file": artifact.name,
                "bytes": artifact.stat().st_size,
                "file_sha256": _sha256_file(artifact),
                "owner_sha256": hashlib.sha256(owner.encode()).hexdigest(),
                "download_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            }
            manifest_temp = directory / "manifest.json.tmp"
            manifest_temp.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            manifest_temp.replace(directory / "manifest.json")
        except Exception:
            shutil.rmtree(directory)
            raise
        return {
            **_public_manifest(manifest),
            "download_url": f"{self.base_url}/downloads/{job_id}?token={token}",
        }

    def status(self, owner, job_id):
        """Return a job's state, refusing a job that is not this owner's.

        Compared in constant time, and a mismatch reports "not found" rather than
        "not yours": the second answer confirms the job exists.
        """
        manifest = self._manifest(job_id)
        if not secrets.compare_digest(
            manifest["owner_sha256"], hashlib.sha256(owner.encode()).hexdigest()
        ):
            raise ValueError("export job was not found")
        return _public_manifest(manifest)

    def download(self, job_id, token, now=None):
        """Release an export's bytes against an unexpired single-use token."""
        manifest = self._manifest(job_id)
        now = int(time.time()) if now is None else now
        if now >= manifest["expires_at_epoch"]:
            raise PermissionError("export download has expired")
        supplied = hashlib.sha256(token.encode()).hexdigest() if isinstance(token, str) else ""
        if not secrets.compare_digest(manifest["download_token_sha256"], supplied):
            raise PermissionError("export download token is invalid")
        artifact = self.root / job_id / manifest["file"]
        if not artifact.is_file() or _sha256_file(artifact) != manifest["file_sha256"]:
            raise ValueError("export artifact integrity check failed")
        return artifact, manifest

    def _manifest(self, job_id):
        if not isinstance(job_id, str) or not re_full_uuid(job_id):
            raise ValueError("export job was not found")
        path = self.root / job_id / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("export job was not found") from exc
        if not isinstance(manifest, dict) or manifest.get("job_id") != job_id:
            raise ValueError("export job manifest is invalid")
        required = {
            "schema_version",
            "job_id",
            "subject",
            "format",
            "batch_id",
            "source_export_sha256",
            "created_at_epoch",
            "expires_at_epoch",
            "tabular_summary",
            "package_summary",
            "status",
            "file",
            "bytes",
            "file_sha256",
            "owner_sha256",
            "download_token_sha256",
        }
        if set(manifest) != required:
            raise ValueError("export job manifest is invalid")
        format_name = manifest.get("format")
        subject = manifest.get("subject")
        integer_fields = ("created_at_epoch", "expires_at_epoch", "bytes")
        tabular_summary = manifest.get("tabular_summary")
        package_summary = manifest.get("package_summary")
        if (
            manifest.get("schema_version") != "crm_downloadable_export_v2"
            or manifest.get("status") != "completed"
            or not isinstance(subject, dict)
            or set(subject) != {"kind", "name", "arguments"}
            or subject.get("kind") not in {"table", "report", "package"}
            or not isinstance(subject.get("name"), str)
            or not isinstance(subject.get("arguments"), dict)
            or not isinstance(manifest.get("batch_id"), str)
            or not manifest["batch_id"]
            or any(
                isinstance(manifest.get(field), bool) or not isinstance(manifest.get(field), int)
                for field in integer_fields
            )
            or manifest["bytes"] < 0
            or manifest["expires_at_epoch"] <= manifest["created_at_epoch"]
            or any(
                not isinstance(manifest.get(field), str)
                or not SHA256_PATTERN.fullmatch(manifest[field])
                for field in (
                    "source_export_sha256",
                    "file_sha256",
                    "owner_sha256",
                    "download_token_sha256",
                )
            )
        ):
            raise ValueError("export job manifest is invalid")
        if subject["kind"] in {"table", "report"}:
            columns = tabular_summary.get("columns") if isinstance(tabular_summary, dict) else None
            if (
                format_name not in TABULAR_FORMATS
                or manifest.get("file") != f"crm_export.{format_name}"
                or package_summary is not None
                or not isinstance(tabular_summary, dict)
                or set(tabular_summary) != {"rows", "columns", "rows_sha256"}
                or isinstance(tabular_summary.get("rows"), bool)
                or not isinstance(tabular_summary.get("rows"), int)
                or tabular_summary["rows"] < 0
                or not isinstance(columns, list)
                or len(columns) > MAX_XLSX_COLUMNS
                or not all(isinstance(column, str) for column in columns)
                or not isinstance(tabular_summary.get("rows_sha256"), str)
                or not SHA256_PATTERN.fullmatch(tabular_summary["rows_sha256"])
            ):
                raise ValueError("export job manifest is invalid")
        else:
            if (
                format_name != "zip"
                or manifest.get("file") != PACKAGE_KIND_TO_ARTIFACT.get(subject["name"])
                or tabular_summary is not None
                or not isinstance(package_summary, dict)
                or package_summary.get("verified") is not True
                or package_summary.get("package_kind") != subject["name"]
                or package_summary.get("manifest")
                not in {"staging_manifest.json", "crm_import_manifest.json"}
                or not isinstance(package_summary.get("findings"), list)
                or not isinstance(package_summary.get("records"), int)
                or package_summary["records"] < 0
                or not isinstance(package_summary.get("source_export_sha256"), str)
                or not SHA256_PATTERN.fullmatch(package_summary["source_export_sha256"])
                or not isinstance(package_summary.get("package_content_sha256"), str)
                or not SHA256_PATTERN.fullmatch(package_summary["package_content_sha256"])
            ):
                raise ValueError("export job manifest is invalid")
        return manifest


def re_full_uuid(value):
    """Return whether a value is a canonical UUID string and nothing else.

    Round-tripped rather than pattern-matched: `UUID()` accepts several
    spellings, and only the one that renders back identically is the ID this
    service issued.
    """
    try:
        return str(uuid.UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False

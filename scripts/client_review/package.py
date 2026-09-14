#!/usr/bin/env python3
"""Create the small client-facing package from safe consolidation JSON.

The consolidation JSON remains the internal audit artifact.  This package is
deliberately limited to an executive summary, group-level decisions, and one
representative card per decision group.  It does not export the exhaustive
queue, a drill-down, or an appendix.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import xlsxwriter
from cli_help import apply_shared_help

from client_review.protection import protected as protected_review

DECISION_HEADERS = (
    "Group",
    "Document type",
    "Issue type",
    "Items",
    "Documents",
    "Internal review?",
    "Fields involved",
    "What we need from you",
    "What happens next",
    "Next review step",
    "Example evidence",
    "Allowed choices",
    "Your choice",
    "Your note",
)
EDITABLE_DECISION_HEADERS = {"Your choice", "Your note"}
MAX_WORKBOOK_BYTES = 25_000_000
MAX_XLSX_MEMBERS = 500
MAX_XLSX_MEMBER_BYTES = 50_000_000
MAX_XLSX_UNCOMPRESSED_BYTES = 150_000_000
REQUIRED_XLSX_MEMBERS = {
    "[Content_Types].xml",
    "xl/_rels/workbook.xml.rels",
    "xl/workbook.xml",
}
# ``xml.etree.ElementTree`` expands internal entities, so a few hundred bytes of
# nested declarations inside a member that satisfies every ZIP bound above can
# still exhaust memory. A workbook produced by Excel, LibreOffice, or XlsxWriter
# declares no DTD and no entities, so rejecting them costs nothing legitimate.
XML_DECLARATION_MARKERS = (b"<!DOCTYPE", b"<!ENTITY", b"<!doctype", b"<!entity")
XML_PREFIX_SCAN_BYTES = 65536
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
PACKAGE_SCHEMA_VERSION = "client_review_package_v1"


def _zip_writestr(archive: zipfile.ZipFile, name: str, value: str | bytes) -> None:
    """Write one deterministic OOXML/ZIP member."""
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, value)


def _sha256(path: Path) -> str:
    """Return the retained byte-level identity of a workbook."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _short(value: Any, limit: int = 180) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _friendly_token(value: Any) -> str:
    text = "" if value is None else str(value)
    replacements = {
        "commission_report": "Commission report",
        "invoice_like": "Invoice or billing document",
        "purchase_order": "Purchase order",
        "statement_like": "Statement",
        "unclassified_document_family": "Document type not yet confirmed",
        "provider_disagreement": "Different readings need a mapping decision",
        "provider_or_schema_exception": "Field or layout is not represented clearly",
        "provider_review_flag_family": "Possible source or handwriting issue",
        "provider_extraction_failure": "Extraction failed",
        "arithmetic_or_reassembly": "Totals or page assembly issue",
        "document_type_proposal": "Document type needs confirmation",
        "no_extractable_text": "Text could not be read",
        "no_high_signal_document_type": "Document type could not be confirmed",
        "google_genai_document_type_proposal": "Document type suggestion",
        "openai_document_type_proposal": "Document type suggestion",
        "openrouter_document_type_proposal": "Document type suggestion",
        "google_genai_document_type_disagrees_with_intake_rule": "Document type differs from the intake rule",
        "openai_document_type_disagrees_with_intake_rule": "Document type differs from the intake rule",
        "openrouter_document_type_disagrees_with_intake_rule": "Document type differs from the intake rule",
        "google_genai_review_flag": "Review note",
        "openai_review_flag": "Review note",
        "no_majority": "The reviewers did not agree",
        "partial_disagreement": "The reviewers partly disagreed",
    }
    if text in replacements:
        return replacements[text]
    return text.replace("_", " ").replace(".", " / ")


def _friendly_text(value: Any, limit: int = 180) -> str:
    text = "" if value is None else str(value)
    for source, target in {
        "google_genai_review_flag:": "Review note: ",
        "openai_review_flag:": "Review note: ",
        "google_genai_document_type_proposal": "Document type suggestion",
        "openai_document_type_proposal": "Document type suggestion",
        "openrouter_document_type_proposal": "Document type suggestion",
        "no_majority": "The reviewers did not agree",
        "partial_disagreement": "The reviewers partly disagreed",
    }.items():
        text = text.replace(source, target)
    return _short(text, limit)


def _source_reference(item: dict[str, Any], items: list[dict[str, Any]]) -> tuple[str, str, str]:
    page_id = str(item.get("page_id") or item.get("document_id") or "")
    prefix = page_id.split("__p", 1)[0] if "__p" in page_id else ""
    source_file = str(item.get("source_file") or "")
    if not source_file and prefix:
        source_file = next(
            (
                str(other.get("source_file"))
                for other in items
                if str(other.get("page_id") or other.get("document_id") or "").startswith(prefix)
                and other.get("source_file")
            ),
            "",
        )
    page_number = str(item.get("source_page_number") or "")
    if not page_number and "__p" in page_id:
        page_number = str(int(page_id.rsplit("__p", 1)[1]))
    return (
        source_file or "Source file recorded in the internal run",
        page_number or "Page number recorded in the internal run",
        page_id,
    )


def _group_label(group: dict[str, Any]) -> str:
    return f"{_friendly_token(group.get('template_family'))} / {_friendly_token(group.get('finding_family'))}"


def _group_protected(group: dict[str, Any]) -> bool:
    """Recompute group protection instead of trusting presentation metadata."""
    finding = str(group.get("finding_family", ""))
    fields = " ".join(str(value) for value in group.get("field_families", []))
    return bool(group.get("protected")) or protected_review({"reason": finding, "field": fields})


def _decision_guidance(group: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    """Return client question, action, rerun scope, and allowed choices."""
    finding = str(group.get("finding_family", ""))
    protected = _group_protected(group)
    if protected:
        return (
            "No client batch decision; retain for internal protected-item review.",
            "Internal review only; do not batch-clear or approve values.",
            "Internal protected-item lane",
            ["Internal only — no client choice"],
        )
    if finding == "document_type_proposal":
        return (
            "Confirm the document family/type for this recurring layout.",
            "Confirm document-family routing, then rerun document classification and extraction.",
            "Document classification + extraction",
            [
                "Confirm document family/type",
                "Provide alternative in comment",
                "Need source sample",
                "Defer",
                "Not applicable",
            ],
        )
    if finding == "no_high_signal_document_type":
        return (
            "Confirm the document type or provide a representative source sample.",
            "Confirm document-type routing, then rerun classification and extraction.",
            "Document classification + extraction",
            [
                "Confirm document type",
                "Provide alternative in comment",
                "Need source sample",
                "Defer",
                "Not applicable",
            ],
        )
    return (
        "Confirm the template-to-field mapping; name any alternative mapping in Client comment.",
        "Apply the client-approved routing/schema proposal, then rerun the affected extraction lane.",
        "Affected extraction lane",
        [
            "Approve proposed routing",
            "Provide alternative in comment",
            "Need source sample",
            "Defer",
            "Not applicable",
        ],
    )


def _validate(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups = payload.get("decision_groups")
    items = payload.get("client_visible_items")
    if not isinstance(groups, list) or not groups:
        raise ValueError("client package requires non-empty decision_groups")
    if not isinstance(items, list):
        raise ValueError("client package requires client_visible_items")
    group_ids = {str(group.get("group_id")) for group in groups}
    if len(group_ids) != len(groups) or "None" in group_ids:
        raise ValueError("decision_groups must have unique group_id values")
    item_ids = {str(item.get("review_item_id")) for item in items}
    if len(item_ids) != len(items) or "None" in item_ids:
        raise ValueError("client_visible_items must have unique review_item_id values")
    grouped_ids = {str(item_id) for group in groups for item_id in group.get("source_item_ids", [])}
    if grouped_ids != item_ids:
        raise ValueError("decision groups must cover every client-visible item exactly")
    if sum(int(group.get("item_count", 0)) for group in groups) != len(items):
        raise ValueError("decision group item counts must reconcile to visible items")
    return groups, items


def _representatives(
    groups: list[dict[str, Any]], items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {str(item["review_item_id"]): item for item in items}
    result = []
    for group in groups:
        representative_id = next(iter(group.get("source_item_ids", [])), "")
        item = by_id.get(str(representative_id), {})
        source_file, source_page, page_id = _source_reference(item, items)
        result.append(
            {
                "group_id": group["group_id"],
                "group": _group_label(group),
                "template_family": _friendly_token(group.get("template_family")),
                "finding_family": _friendly_token(group.get("finding_family")),
                "items": int(group.get("item_count", 0)),
                "documents": int(group.get("document_count", 0)),
                "protected": "Yes" if _group_protected(group) else "No",
                "representative_item": representative_id,
                "document_id": item.get("document_id", ""),
                "source_file": source_file,
                "source_page": source_page,
                "page_id": page_id,
                "field": _friendly_token(item.get("field", "")),
                "reason": _friendly_text(
                    item.get("reason") or (group.get("representative_reasons") or [""])[0]
                ),
                "evidence": _friendly_text(item.get("evidence_text", "")),
                "client_question": _decision_guidance(group)[0],
            }
        )
    return result


def _write_docx_guide(
    payload: dict[str, Any], groups: list[dict[str, Any]], items: list[dict[str, Any]], output: Path
) -> None:
    """Write a plain-language Word guide without adding a runtime dependency."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    esc = html.escape
    summary = payload.get("summary", {})
    document_count = len(
        {str(item.get("document_id")) for item in items if item.get("document_id")}
    )
    protected_groups = sum(
        1
        for group in groups
        if _decision_guidance(group)[3] == ["Internal only — no client choice"]
    )
    client_groups = len(groups) - protected_groups
    paragraphs: list[str] = []

    def add(text: str, style: str = "Normal") -> None:
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>'
        )

    def bullet(text: str) -> None:
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="ListBullet"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>'
        )

    def numbered(text: str) -> None:
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="ListNumber"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>'
        )

    add("Client Review Guide", "Title")
    add("One simple review pass for the document issues in this package.", "Subtitle")
    add("What this package is for", "Heading1")
    add(
        f"This workbook turns {len(items):,} individual review items into {len(groups)} issue groups. You only need to make choices for {client_groups} groups. The other {protected_groups} groups are included so nothing is lost, but they require internal review and do not ask you to make a decision."
    )
    add("What you need to do", "Heading1")
    for text in [
        "Open client_review_package.xlsx and start on the Executive Summary tab.",
        "Open the Decisions Needed tab. Each row represents one recurring issue group.",
        "For rows with a client choice, select one option in the Your choice column.",
        "If you choose Provide alternative in comment, write the exact correction or preferred mapping in Your note.",
        "Leave rows marked Internal only — no client choice unchanged.",
        "Return the completed workbook. No other review list is needed for this pass.",
    ]:
        numbered(text)
    add("What the choices mean", "Heading1")
    for text in [
        "Approve proposed routing: use the proposed document type or field mapping, then check the results again.",
        "Confirm document family/type or Confirm document type: tell us what kind of document this repeated layout represents.",
        "Provide alternative in comment: describe the correct document type, field, or column mapping.",
        "Need source sample: indicate that a representative original document is needed before deciding.",
        "Source/rescan available or No source/rescan: tell us whether a clearer source can be supplied.",
        "Defer: leave the group unresolved for a later decision.",
        "Not applicable: the proposed question does not apply to this group.",
    ]:
        bullet(text)
    add("What happens after you return it", "Heading1")
    add(
        "We will use the completed choices to prepare the next processing run, recheck the affected documents, and report what changed. A choice about routing or document type does not approve a financial amount, handwriting reading, or extracted value. Those protected issues stay in internal review until they have their required checks."
    )
    add("What the numbers mean", "Heading1")
    for text in [
        f"Records retained for review: {int(summary.get('source_items', len(items))):,}",
        f"Items represented in this workbook: {len(items):,}",
        f"Issue groups: {len(groups)}",
        f"Documents represented: {document_count:,}",
        f"Items requiring internal review: {sum(int(group.get('item_count', 0)) for group in groups if _group_protected(group)):,}",
    ]:
        bullet(text)
    add("Important", "Heading1")
    add(
        "The detailed item-by-item review list and technical processing records are intentionally not included in the client package. The workbook is the complete client decision surface for this pass."
    )

    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{w}"><w:body>{"".join(paragraphs)}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708"/></w:sectPr></w:body></w:document>'''
    styles_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{w}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults><w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:style><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:after="120"/></w:pPr><w:rPr><w:b/><w:color w:val="17365D"/><w:sz w:val="48"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:color w:val="5B6573"/><w:sz w:val="26"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:pPr><w:spacing w:before="320" w:after="160"/></w:pPr><w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="32"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:pPr><w:ind w:left="720" w:hanging="360"/><w:spacing w:after="80"/></w:pPr></w:style><w:style w:type="paragraph" w:styleId="ListNumber"><w:name w:val="List Number"/><w:pPr><w:ind w:left="720" w:hanging="360"/><w:spacing w:after="80"/></w:pPr></w:style></w:styles>'''
    numbering_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering xmlns:w="{w}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum><w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>'''
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/></Types>"""
    rels = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        _zip_writestr(archive, "[Content_Types].xml", content_types)
        _zip_writestr(
            archive,
            "_rels/.rels",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{rels}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''',
        )
        _zip_writestr(
            archive,
            "word/_rels/document.xml.rels",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{rels}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>''',
        )
        _zip_writestr(archive, "word/document.xml", document_xml)
        _zip_writestr(archive, "word/styles.xml", styles_xml)
        _zip_writestr(archive, "word/numbering.xml", numbering_xml)


def _write_decision_pages_docx(
    groups: list[dict[str, Any]], items: list[dict[str, Any]], output: Path
) -> None:
    """Write one plain-language page for each group requiring client input."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    esc = html.escape
    by_id = {str(item["review_item_id"]): item for item in items}
    client_groups = [
        group
        for group in groups
        if _decision_guidance(group)[3] != ["Internal only — no client choice"]
    ]
    paragraphs: list[str] = []

    def add(text: str, style: str = "Normal", page_break: bool = False) -> None:
        br = '<w:br w:type="page"/>' if page_break else ""
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{br}<w:r><w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>'
        )

    for index, group in enumerate(client_groups, start=1):
        item = by_id.get(str(next(iter(group.get("source_item_ids", [])), "")), {})
        source_file, source_page, page_id = _source_reference(item, items)
        requested, action, scope, choices = _decision_guidance(group)
        add(f"Client Decision {index} of {len(client_groups)}", "Title", page_break=index > 1)
        add(_group_label(group), "Subtitle")
        add("Decision needed", "Heading1")
        add(requested)
        add("Group details", "Heading1")
        add(
            f"Document type: {_friendly_token(group.get('template_family'))} | Issue: {_friendly_token(group.get('finding_family'))} | Items: {int(group.get('item_count', 0)):,} | Documents: {int(group.get('document_count', 0)):,}"
        )
        fields = (
            "; ".join(_friendly_token(field) for field in (group.get("field_families") or [])[:10])
            or "No specific field was identified."
        )
        add(f"Fields involved: {fields}")
        add("Exact source page", "Heading1")
        add(
            f"Source file: {source_file} | Page: {source_page} | Page ID: {page_id or 'not recorded'}"
        )
        add(
            f"Document ID: {item.get('document_id') or 'not recorded'} | Field: {_friendly_token(item.get('field')) or 'not recorded'}"
        )
        add("Example from the source", "Heading1")
        add(
            _friendly_text(
                item.get("evidence_text")
                or " | ".join((group.get("representative_reasons") or [])[:2]),
                360,
            )
        )
        add("Choices in the workbook", "Heading1")
        add("Choose one: " + "; ".join(choices))
        add("After your choice", "Heading1")
        add(action)
        add(f"Next review step: {scope}")
        add("Record it in the workbook", "Heading1")
        add(
            "In the Decisions Needed tab, find this Group and complete Your choice. Use Your note for an alternative or clarification."
        )

    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{w}"><w:body>{"".join(paragraphs)}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="864" w:right="864" w:bottom="864" w:left="864" w:header="500" w:footer="500"/></w:sectPr></w:body></w:document>'''
    styles_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{w}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="19"/></w:rPr></w:rPrDefault></w:docDefaults><w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="60" w:line="240" w:lineRule="auto"/></w:pPr></w:style><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:b/><w:color w:val="17365D"/><w:sz w:val="40"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:pPr><w:spacing w:after="100"/></w:pPr><w:rPr><w:color w:val="5B6573"/><w:sz w:val="22"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:pPr><w:spacing w:before="120" w:after="60"/></w:pPr><w:rPr><w:b/><w:color w:val="2E74B5"/><w:sz w:val="26"/></w:rPr></w:style></w:styles>'''
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>"""
    rels = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        _zip_writestr(archive, "[Content_Types].xml", content_types)
        _zip_writestr(
            archive,
            "_rels/.rels",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{rels}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''',
        )
        _zip_writestr(
            archive,
            "word/_rels/document.xml.rels",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{rels}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''',
        )
        _zip_writestr(archive, "word/document.xml", document_xml)
        _zip_writestr(archive, "word/styles.xml", styles_xml)


def _write_workbook(
    payload: dict[str, Any], groups: list[dict[str, Any]], items: list[dict[str, Any]], output: Path
) -> None:
    representatives = _representatives(groups, items)
    visible_count = len(items)
    source_count = int(payload.get("summary", {}).get("source_items", visible_count))
    covered_count = int(payload.get("summary", {}).get("covered_dependency_items", 0))
    protected_count = sum(
        int(group.get("item_count", 0)) for group in groups if _group_protected(group)
    )
    document_count = len(
        {str(item.get("document_id")) for item in items if item.get("document_id")}
    )
    largest = sorted(groups, key=lambda group: int(group.get("item_count", 0)), reverse=True)[:5]

    workbook = xlsxwriter.Workbook(
        str(output), {"strings_to_formulas": False, "strings_to_urls": False}
    )
    workbook.set_properties({"created": datetime(1980, 1, 1)})
    title = workbook.add_format(
        {"bold": True, "font_size": 16, "font_color": "#FFFFFF", "bg_color": "#17365D"}
    )
    section = workbook.add_format({"bold": True, "font_color": "#17365D", "bg_color": "#D9EAF7"})
    header = workbook.add_format(
        {"bold": True, "font_color": "#17365D", "bg_color": "#D9EAF7", "text_wrap": True}
    )
    body = workbook.add_format({"text_wrap": True, "valign": "top"})
    note = workbook.add_format({"italic": True, "font_color": "#667085", "text_wrap": True})
    number = workbook.add_format({"bold": True, "font_color": "#17365D", "num_format": "#,##0"})
    protected = workbook.add_format({"bg_color": "#FCE4D6", "bold": True})

    summary = workbook.add_worksheet("Executive Summary")
    summary.hide_gridlines(2)
    summary.merge_range("A1:H1", "Client Review Package — Executive Summary", title)
    summary.merge_range(
        "A2:H2",
        "Start with the issue groups; the detailed item-by-item list is kept internal.",
        note,
    )
    summary.write_column(
        "A4:A9",
        [
            "Records retained for review",
            "Items represented in this workbook",
            "Issue groups",
            "Documents represented",
            "Items requiring internal review",
            "Related items kept internal",
        ],
        section,
    )
    summary.write_column(
        "B4:B9",
        [source_count, visible_count, len(groups), document_count, protected_count, covered_count],
        number,
    )
    summary.merge_range("D4:H4", "Recommended client sequence", section)
    sequence = [
        ["1", "Confirm document types and mappings", "Start with the largest repeated groups."],
        ["2", "Run the affected documents again", "We will handle the next processing step."],
        ["3", "Review internal exceptions", "Protected issues stay internal."],
        ["4", "Return this workbook", "No item-by-item list is needed."],
    ]
    for row, values in enumerate(sequence, start=4):
        summary.write_row(row, 3, values, body)
    summary.write_row(
        10,
        0,
        [
            "Group",
            "Issue group",
            "Items",
            "Documents",
            "Internal review?",
            "Example",
            "Suggested next step",
            "Client note",
        ],
        header,
    )
    for row, group in enumerate(largest, start=11):
        summary.write_row(
            row,
            0,
            [
                group["group_id"],
                _group_label(group),
                group["item_count"],
                group["document_count"],
                "Yes" if _group_protected(group) else "No",
                _friendly_text(" | ".join((group.get("representative_reasons") or [])[:2]), 140),
                "Internal review only" if _group_protected(group) else "Make the requested choice",
                "",
            ],
            body,
        )
    summary.merge_range("A18:H18", "Important interpretation", section)
    summary.merge_range(
        "A19:H21",
        f"The {covered_count:,} related items are kept internal so the client can work from {len(groups)} clear issue groups. The complete item-by-item review is retained internally.",
        body,
    )
    summary.set_column("A:A", 30)
    summary.set_column("B:B", 30)
    summary.set_column("C:C", 12)
    summary.set_column("D:D", 12)
    summary.set_column("E:E", 30)
    summary.set_column("F:F", 48)
    summary.set_column("G:G", 26)
    summary.set_column("H:H", 22)
    summary.freeze_panes(10, 0)

    decisions = workbook.add_worksheet("Decisions Needed")
    decisions.hide_gridlines(2)
    decisions.write(0, 14, "package_schema_version")
    decisions.write(0, 15, PACKAGE_SCHEMA_VERSION)
    decisions.write(1, 14, "safe_consolidation_sha256")
    decisions.write(1, 15, _json_sha256(payload))
    decisions.write(2, 14, "decision_group_ids_sha256")
    decisions.write(2, 15, _json_sha256([group["group_id"] for group in groups]))
    decisions.set_column("O:P", None, None, {"hidden": True})
    decisions.merge_range("A1:N1", "Decisions Needed — One Client Review", title)
    decisions.merge_range(
        "A2:N2",
        "Complete only Your choice and Your note. Select one of the choices shown in Allowed choices. A choice directs the next check; it does not approve a value.",
        note,
    )
    decisions.write_row(
        3,
        0,
        list(DECISION_HEADERS),
        header,
    )
    for row, group in enumerate(groups, start=4):
        requested, action, scope, allowed_choices = _decision_guidance(group)
        choices = " | ".join(allowed_choices)
        decisions.write_row(
            row,
            0,
            [
                group["group_id"],
                _friendly_token(group.get("template_family")),
                _friendly_token(group.get("finding_family")),
                group["item_count"],
                group["document_count"],
                "Yes" if _group_protected(group) else "No",
                "; ".join(
                    _friendly_token(field) for field in (group.get("field_families") or [])[:8]
                ),
                requested,
                action,
                scope,
                _friendly_text(" | ".join((group.get("representative_reasons") or [])[:3]), 260),
                choices,
                "",
                "",
            ],
            body,
        )
        if _group_protected(group):
            decisions.write(row, 5, "Yes", protected)
        else:
            decisions.data_validation(
                row,
                12,
                row,
                12,
                {
                    "validate": "list",
                    "source": allowed_choices,
                    "input_title": "Client decision",
                    "input_message": "Choose one of the Allowed choices. Describe alternatives in Client comment.",
                },
            )
    decisions.write_comment(
        "M4",
        "Choose a value from the list. For an alternative, describe the exact mapping in Client comment.",
    )
    decisions.set_column("A:A", 20)
    decisions.set_column("B:B", 22)
    decisions.set_column("C:C", 30)
    decisions.set_column("D:E", 12)
    decisions.set_column("F:F", 12)
    decisions.set_column("G:G", 42)
    decisions.set_column("H:H", 48)
    decisions.set_column("I:I", 54)
    decisions.set_column("J:J", 26)
    decisions.set_column("K:K", 52)
    decisions.set_column("L:L", 64)
    decisions.set_column("M:M", 28)
    decisions.set_column("N:N", 34)
    decisions.freeze_panes(4, 0)

    cards = workbook.add_worksheet("Representative Cards")
    cards.hide_gridlines(2)
    cards.merge_range("A1:P1", "Examples — One Per Issue Group", title)
    cards.merge_range(
        "A2:P2",
        "Each example includes the exact source-page reference used for the decision. The complete item-by-item list remains internal.",
        note,
    )
    card_headers = [
        "Group",
        "Issue group",
        "Document type",
        "Issue type",
        "Items",
        "Documents",
        "Internal review?",
        "Example item",
        "Document",
        "Source file",
        "Source page",
        "Page ID",
        "Field",
        "Why it was flagged",
        "Example from source",
        "What to do",
    ]
    cards.write_row(3, 0, card_headers, header)
    for row, card in enumerate(representatives, start=4):
        cards.write_row(
            row,
            0,
            [
                card[key]
                for key in [
                    "group_id",
                    "group",
                    "template_family",
                    "finding_family",
                    "items",
                    "documents",
                    "protected",
                    "representative_item",
                    "document_id",
                    "source_file",
                    "source_page",
                    "page_id",
                    "field",
                    "reason",
                    "evidence",
                    "client_question",
                ]
            ],
            body,
        )
        if card["protected"] == "Yes":
            cards.write(row, 6, "Yes", protected)
    cards.set_column("A:A", 20)
    cards.set_column("B:B", 30)
    cards.set_column("C:D", 22)
    cards.set_column("E:F", 12)
    cards.set_column("G:G", 12)
    cards.set_column("H:H", 20)
    cards.set_column("I:I", 28)
    cards.set_column("J:J", 40)
    cards.set_column("K:K", 12)
    cards.set_column("L:L", 30)
    cards.set_column("M:M", 24)
    cards.set_column("N:O", 48)
    cards.set_column("P:P", 46)
    cards.freeze_panes(4, 0)
    workbook.close()


def write_client_review_package(payload: dict[str, Any], output_dir: Path) -> Path:
    """Write the client package and a no-clobber ZIP containing its deliverables."""
    groups, items = _validate(payload)
    if output_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite existing client package directory: {output_dir}"
        )
    zip_path = output_dir.parent / f"{output_dir.name}.zip"
    if zip_path.exists():
        raise FileExistsError(f"refusing to overwrite existing client package ZIP: {zip_path}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir()
    workbook = output_dir / "client_review_package.xlsx"
    _write_workbook(payload, groups, items, workbook)
    _write_docx_guide(payload, groups, items, output_dir / "client_review_guide.docx")
    _write_decision_pages_docx(groups, items, output_dir / "decision_pages.docx")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.iterdir()):
            _zip_writestr(archive, path.name, path.read_bytes())
    return output_dir


def _xlsx_cell_values(path: Path, sheet_name: str) -> list[list[str]]:
    """Read simple string/number cells without adding a workbook dependency."""
    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))  # noqa: S314 - the archive preflight rejects any DTD or entity declaration before this parse
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))  # noqa: S314 - the archive preflight rejects any DTD or entity declaration before this parse
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        sheets = workbook.find("m:sheets", ns)
        sheet = (
            next((s for s in sheets if s.attrib.get("name") == sheet_name), None)
            if sheets is not None
            else None
        )
        if sheet is None:
            raise ValueError(f"workbook does not contain a {sheet_name!r} worksheet")
        relationship = rel_map.get(sheet.attrib[f"{{{ns['r']}}}id"])
        if relationship is None:
            raise ValueError(f"workbook has no relationship target for {sheet_name!r}")
        target = relationship.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        if target not in archive.namelist():
            raise ValueError(f"workbook worksheet part is missing: {target}")
        root = ET.fromstring(archive.read(target))  # noqa: S314 - the archive preflight rejects any DTD or entity declaration before this parse
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))  # noqa: S314 - the archive preflight rejects any DTD or entity declaration before this parse
            shared = [
                "".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")) for si in shared_root
            ]
        rows: list[list[str]] = []
        for row in root.findall(".//m:sheetData/m:row", ns):
            values: dict[int, str] = {}
            for cell in row.findall("m:c", ns):
                match = re.match(r"([A-Z]+)", cell.attrib["r"])
                if not match:
                    continue
                col = 0
                for char in match.group(1):
                    col = col * 26 + ord(char) - 64
                col -= 1
                value = cell.find("m:v", ns)
                text = "" if value is None else (value.text or "")
                if cell.attrib.get("t") == "inlineStr":
                    # A returned workbook is written by whatever spreadsheet the
                    # client used, and some write text inline rather than into the
                    # shared table. Reading only <v> left those cells empty, so a
                    # client's answer was recorded as unanswered rather than
                    # refused -- a silent loss on the path that authorizes change.
                    inline = cell.find("m:is", ns)
                    text = (
                        "".join(node.text or "" for node in inline.iter(f"{{{ns['m']}}}t"))
                        if inline is not None
                        else ""
                    )
                elif cell.attrib.get("t") == "s" and text:
                    index = int(text) if text.isdigit() else -1
                    if not 0 <= index < len(shared):
                        raise ValueError("workbook cell references an unknown shared string")
                    text = shared[index]
                values[col] = text
            rows.append([values.get(i, "") for i in range(max(values, default=-1) + 1)])
        return rows


def _decision_rows(workbook: Path) -> tuple[list[str], list[list[str]]]:
    """Read and validate the complete Decisions Needed table contract."""
    rows = _xlsx_cell_values(workbook, "Decisions Needed")
    header_index = next(
        (index for index, row in enumerate(rows) if row and row[0] == "Group"), None
    )
    if header_index is None:
        raise ValueError("workbook does not contain the current Decisions Needed contract")
    header = rows[header_index]
    if header != list(DECISION_HEADERS):
        raise ValueError("Decisions Needed sheet headers differ from the exact contract")
    data_rows = []
    for row in rows[header_index + 1 :]:
        padded = row + [""] * (len(DECISION_HEADERS) - len(row))
        if not any(padded):
            continue
        if not padded[0]:
            raise ValueError("Decisions Needed contains a populated row without a Group")
        data_rows.append(padded[: len(DECISION_HEADERS)])
    group_ids = [row[0] for row in data_rows]
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("Decisions Needed contains duplicate Group values")
    return list(DECISION_HEADERS), data_rows


def _package_metadata(workbook: Path) -> dict[str, str]:
    """Read the hidden immutable package lineage from the decisions sheet."""
    values = {}
    for row in _xlsx_cell_values(workbook, "Decisions Needed"):
        if len(row) >= 16 and row[14] in {
            "package_schema_version",
            "safe_consolidation_sha256",
            "decision_group_ids_sha256",
        }:
            values[row[14]] = row[15]
    if (
        values.get("package_schema_version") != PACKAGE_SCHEMA_VERSION
        or not re.fullmatch(r"[0-9a-f]{64}", values.get("safe_consolidation_sha256", ""))
        or not re.fullmatch(r"[0-9a-f]{64}", values.get("decision_group_ids_sha256", ""))
    ):
        raise ValueError("workbook is missing valid immutable package lineage")
    return values


def preserve_client_responses(
    workbook: Path, output: Path, issued_workbook: Path | None = None
) -> dict[str, Any]:
    """Write an immutable, proposal-only snapshot of client response fields.

    This intentionally remains useful when a returned workbook cannot yet pass
    the full importer (for example, a legacy workbook without package
    lineage).  It never changes the workbook and records any contract or
    lineage exception beside the exact editable values.
    """
    if output.exists():
        raise FileExistsError(f"refusing to overwrite preserved responses: {output}")
    _validate_workbook_archive(workbook)
    header, rows = _decision_rows(workbook)
    positions = {name: index for index, name in enumerate(header)}
    issued_rows: list[list[str]] | None = None
    issued_metadata: dict[str, str] | None = None
    fixed_cell_differences: list[str] = []
    lineage_status = "not_compared"
    lineage_exception = ""
    issued_hash = ""
    if issued_workbook is not None:
        _validate_workbook_archive(issued_workbook)
        issued_hash = _sha256(issued_workbook)
        _, issued_rows = _decision_rows(issued_workbook)
        if [row[0] for row in rows] != [row[0] for row in issued_rows]:
            lineage_status = "group_rows_differ"
            lineage_exception = "returned workbook Group rows differ from issued workbook"
        else:
            fixed_positions = [
                index for index, name in enumerate(header) if name not in EDITABLE_DECISION_HEADERS
            ]
            fixed_cell_differences = [
                issued[positions["Group"]]
                for issued, returned in zip(issued_rows, rows, strict=True)
                if any(issued[index] != returned[index] for index in fixed_positions)
            ]
            if fixed_cell_differences:
                lineage_status = "fixed_cells_changed"
                lineage_exception = "returned workbook changed fixed cells"
            else:
                try:
                    issued_metadata = _package_metadata(issued_workbook)
                except ValueError as exc:
                    lineage_status = "metadata_missing_or_invalid"
                    lineage_exception = str(exc)
                else:
                    try:
                        returned_metadata = _package_metadata(workbook)
                    except ValueError as exc:
                        lineage_status = "metadata_missing_or_invalid"
                        lineage_exception = str(exc)
                    else:
                        if returned_metadata != issued_metadata:
                            lineage_status = "metadata_changed"
                            lineage_exception = (
                                "returned workbook changed immutable package lineage"
                            )
                        else:
                            lineage_status = "verified"
    all_rows = [
        {
            "decision_id": row[positions["Group"]],
            "your_choice": row[positions["Your choice"]],
            "your_note": row[positions["Your note"]],
        }
        for row in rows
    ]
    responses = [row for row in all_rows if row["your_choice"] or row["your_note"]]
    result = {
        "artifact_type": "client_response_preservation",
        "schema_version": "1.0",
        "proposal_only": True,
        "returned_workbook_sha256": _sha256(workbook),
        "issued_workbook_sha256": issued_hash,
        "decision_group_count": len(rows),
        "decision_group_ids_sha256": _json_sha256([row["decision_id"] for row in all_rows]),
        "lineage_status": lineage_status,
        "lineage_exception": lineage_exception,
        "fixed_cell_differences": fixed_cell_differences,
        "response_count": len(responses),
        "responses": responses,
        "all_decision_rows": all_rows,
    }
    if issued_metadata is not None:
        result["safe_consolidation_sha256"] = issued_metadata["safe_consolidation_sha256"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def _validate_workbook_archive(workbook: Path) -> None:
    """Bound untrusted returned-workbook ZIP expansion before parsing XML."""
    if not workbook.is_file():
        raise ValueError(f"workbook does not exist: {workbook}")
    if workbook.stat().st_size > MAX_WORKBOOK_BYTES:
        raise ValueError("workbook exceeds the compressed-size limit")
    try:
        with zipfile.ZipFile(workbook) as archive:
            members = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ValueError("workbook is not a valid XLSX ZIP archive") from exc
    if len(members) > MAX_XLSX_MEMBERS:
        raise ValueError("workbook contains too many ZIP members")
    if any(member.file_size > MAX_XLSX_MEMBER_BYTES for member in members):
        raise ValueError("workbook contains an oversized uncompressed member")
    if sum(member.file_size for member in members) > MAX_XLSX_UNCOMPRESSED_BYTES:
        raise ValueError("workbook exceeds the total uncompressed-size limit")
    if not REQUIRED_XLSX_MEMBERS.issubset({member.filename for member in members}):
        raise ValueError("workbook is missing required XLSX package members")
    with zipfile.ZipFile(workbook) as archive:
        for member in members:
            if not member.filename.lower().endswith((".xml", ".rels")):
                continue
            with archive.open(member) as stream:
                head = stream.read(XML_PREFIX_SCAN_BYTES)
            if any(marker in head for marker in XML_DECLARATION_MARKERS):
                raise ValueError(f"workbook XML declares a DTD or entity: {member.filename}")


def import_client_decisions(workbook: Path, output: Path, issued_workbook: Path) -> dict[str, Any]:
    """Validate a returned workbook against the immutable issued workbook."""
    if workbook.resolve() == issued_workbook.resolve():
        raise ValueError("returned workbook must be separate from the immutable issued workbook")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing decision proposal: {output}")
    _validate_workbook_archive(workbook)
    _validate_workbook_archive(issued_workbook)
    returned_metadata = _package_metadata(workbook)
    issued_metadata = _package_metadata(issued_workbook)
    if returned_metadata != issued_metadata:
        raise ValueError("returned workbook changed immutable package lineage")
    header, rows = _decision_rows(workbook)
    _, issued_rows = _decision_rows(issued_workbook)
    positions = {name: index for index, name in enumerate(header)}
    issued_ids = [row[positions["Group"]] for row in issued_rows]
    returned_ids = [row[positions["Group"]] for row in rows]
    if returned_ids != issued_ids:
        raise ValueError("returned workbook Group rows differ from the issued workbook")
    fixed_positions = [
        index for index, name in enumerate(header) if name not in EDITABLE_DECISION_HEADERS
    ]
    for issued, returned in zip(issued_rows, rows, strict=True):
        if any(issued[index] != returned[index] for index in fixed_positions):
            raise ValueError(
                f"returned workbook changed fixed cells for Group {issued[positions['Group']]}"
            )
    decisions = []
    unresolved: list[str] = []
    invalid: list[dict[str, str]] = []
    for issued_row, row in zip(issued_rows, rows, strict=True):
        decision_id = row[positions["Group"]]
        protected = issued_row[positions["Internal review?"]] == "Yes"
        client_decision = row[positions["Your choice"]]
        allowed = [
            choice.strip()
            for choice in issued_row[positions["Allowed choices"]].split("|")
            if choice.strip()
        ]
        if protected:
            if client_decision:
                invalid.append(
                    {
                        "decision_id": decision_id,
                        "reason": "protected group cannot receive a client decision",
                    }
                )
        elif not client_decision:
            unresolved.append(decision_id)
        elif client_decision not in allowed:
            invalid.append(
                {"decision_id": decision_id, "reason": f"invalid decision: {client_decision}"}
            )
        elif (
            client_decision == "Provide alternative in comment"
            and not row[positions["Your note"]].strip()
        ):
            invalid.append(
                {
                    "decision_id": decision_id,
                    "reason": "alternative decision requires Your note",
                }
            )
        decisions.append(
            {
                "decision_id": decision_id,
                "template_family": issued_row[positions["Document type"]],
                "finding_family": issued_row[positions["Issue type"]],
                "proposed_fields": issued_row[positions["Fields involved"]],
                "proposed_action": issued_row[positions["What happens next"]],
                "rerun_scope": issued_row[positions["Next review step"]],
                "client_decision": client_decision,
                "client_comment": row[positions["Your note"]],
                "protected": protected,
                "proposal_only": True,
            }
        )
    result = {
        "proposal_only": True,
        "issued_workbook_sha256": _sha256(issued_workbook),
        "returned_workbook_sha256": _sha256(workbook),
        "safe_consolidation_sha256": issued_metadata["safe_consolidation_sha256"],
        "decision_group_ids_sha256": issued_metadata["decision_group_ids_sha256"],
        "issued_group_count": len(issued_rows),
        "complete": not unresolved and not invalid,
        "decision_count": len(decisions),
        "unresolved_decision_ids": unresolved,
        "invalid_decisions": invalid,
        "decisions": decisions,
    }
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    create = subparsers.add_parser("create")
    create.add_argument(
        "consolidation",
        type=Path,
        help="Safe-consolidation artifact the issued workbook is built from.",
    )
    create.add_argument("--out-dir", type=Path, required=True)
    import_parser = subparsers.add_parser("import-decisions")
    import_parser.add_argument(
        "workbook",
        type=Path,
        help="Returned client workbook. Untrusted input, validated against the issued copy before it is read.",
    )
    import_parser.add_argument(
        "--issued-workbook",
        type=Path,
        required=True,
        help="The exact workbook that was issued. The return is validated against it.",
    )
    import_parser.add_argument("--out", type=Path, required=True)
    preserve_parser = subparsers.add_parser(
        "preserve-responses",
        help="snapshot exact client choices and notes without applying them",
    )
    preserve_parser.add_argument(
        "workbook",
        type=Path,
        help="Returned client workbook whose editable responses are preserved unchanged.",
    )
    preserve_parser.add_argument(
        "--issued-workbook",
        type=Path,
        help="The exact workbook that was issued. The return is validated against it.",
    )
    preserve_parser.add_argument("--out", type=Path, required=True)
    apply_shared_help(parser)
    args = parser.parse_args()
    if args.command == "import-decisions":
        result = import_client_decisions(args.workbook, args.out, args.issued_workbook)
        if not result["complete"]:
            print("client decisions are incomplete or invalid; proposal artifact is blocked")
            return 2
    elif args.command == "preserve-responses":
        preserve_client_responses(args.workbook, args.out, args.issued_workbook)
    else:
        if args.command is None:
            parser.error("choose create, preserve-responses, or import-decisions")
        payload = json.loads(args.consolidation.read_text(encoding="utf-8"))
        write_client_review_package(payload, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

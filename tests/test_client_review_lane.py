"""Cover the unified client review lane end to end.

The lane's value is that a client gets one consistent pack from any blocked
control, so the tests are written against that promise: the same artifacts always
produce the same three deliverables, a threshold decides whether to ask rather
than what to keep, and no question ever claims to clear a protected finding.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

evidence = importlib.import_module("client_review.evidence")
instructions = importlib.import_module("client_review.instructions")
lane = importlib.import_module("client_review.lane")
questions = importlib.import_module("client_review.questions")
thresholds = importlib.import_module("client_review.thresholds")
workbook = importlib.import_module("client_review.workbook")
cli = importlib.import_module("client_review_lane")

JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffc00011080001000103012200021101031101ffc400"
    "1f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303"
    "020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c115"
    "52d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a53545556"
    "5758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4"
    "a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7"
    "e8e9eaf1f2f3f4f5f6f7f8f9faffda000c03010002110311003f00fefe28a28a00ffd9"
)


def exceptions_file(tmp_path: Path, name: str, items: list[dict]) -> Path:
    """Write an artifact shaped like any control's exception output."""
    path = tmp_path / name
    path.write_text(json.dumps({"summary": {"count": len(items)}, "exceptions": items}))
    return path


def consensus_item(index: int, reason: str = "partial_disagreement") -> dict:
    """Build one consensus-style finding."""
    return {
        "document_id": f"doc-{index:03d}",
        "page_id": f"doc-{index:03d}",
        "field": f"lines[{index}].description",
        "reason": reason,
        "flag": "no_consensus",
        "rule": "printed_majority",
    }


def manifest_file(tmp_path: Path, page_ids: list[str], real_pdf: bool = False) -> Path:
    """Write an intake manifest whose page paths resolve beside it."""
    root = tmp_path / "pages"
    (root / "pages").mkdir(parents=True, exist_ok=True)
    records = []
    for page_id in page_ids:
        relative = f"pages/{page_id}.pdf"
        if real_pdf:
            (root / relative).write_bytes(b"%PDF-1.4\n")
        records.append({"page_id": page_id, "page_pdf": relative})
    path = root / "ingestion_manifest.json"
    path.write_text(json.dumps({"summary": {}, "pages": records}))
    return path


def test_thresholds_name_the_control_and_respect_its_floor(monkeypatch):
    """A control is identified from its own vocabulary, not a generic label."""
    assert thresholds.classify({"reason": "partial_disagreement"}) == "consensus"
    assert thresholds.classify({"source_artifact": "arithmetic_exceptions.json"}) == "arithmetic"
    assert thresholds.classify({"review_source": "handwriting_review"}) == "handwriting"
    assert thresholds.classify({"reason": "something new"}) == thresholds.UNCLASSIFIED_BLOCK

    assert thresholds.blocking({"disposition": "client_review_required"})
    assert not thresholds.blocking({"blocking": False})
    assert not thresholds.blocking({"disposition": "informational"})

    items = [consensus_item(index) for index in range(3)]
    report = thresholds.evaluate(items)
    assert report["triggered_blocks"] == ["consensus"]
    assert report["blocking_item_count"] == 3

    # Raising the floor above the count stops the ask without touching the queue.
    monkeypatch.setenv("CLIENT_REVIEW_LANE_CONSENSUS_THRESHOLD", "10")
    quiet = thresholds.evaluate(items)
    assert quiet["triggered"] is False
    assert quiet["blocks"][0]["blocking_items"] == 3

    monkeypatch.setenv("CLIENT_REVIEW_LANE_CONSENSUS_THRESHOLD", "-1")
    with pytest.raises(ValueError, match="zero or greater"):
        thresholds.threshold_for("consensus")
    with pytest.raises(ValueError, match="unknown review block"):
        thresholds.threshold_for("not_a_control")

    monkeypatch.setenv("CLIENT_REVIEW_LANE_CONSENSUS_THRESHOLD", "1")
    monkeypatch.setenv("CLIENT_REVIEW_LANE_ENABLED", "false")
    assert thresholds.evaluate(items)["lane_enabled"] is False


def test_questions_reduce_the_queue_without_claiming_to_clear_it():
    """Repeated causes become one question, and protection blocks only clearing."""
    items = [consensus_item(index) for index in range(6)]
    groups = [
        {
            "group_id": "decision_group:00001",
            "template_family": "commission_report",
            "finding_family": "provider_disagreement",
            "field_families": ["line_description"],
            "item_count": 5,
            "document_count": 5,
            "document_ids": [f"doc-{i:03d}" for i in range(5)],
            "source_item_indexes": [0, 1, 2, 3, 4],
            "representative_reasons": ["partial_disagreement"],
            "protected": True,
        },
        {
            "group_id": "decision_group:00002",
            "template_family": "commission_report",
            "finding_family": "document_type_proposal",
            "field_families": ["document_level"],
            "item_count": 1,
            "document_count": 1,
            "document_ids": ["doc-005"],
            "source_item_indexes": [5],
            "representative_reasons": ["document_type_proposal"],
            "protected": False,
        },
    ]
    built = questions.build_questions(groups, items)
    first, second = built["questions"]
    # Ordering puts the unprotected, highest-yield question first.
    assert first["finding_family"] == "document_type_proposal"
    protected_question = second
    assert protected_question["protected"] is True
    # Rule 7: asked, but never cleared in bulk.
    assert protected_question["client_answer_permitted"] is True
    assert protected_question["batch_clear_permitted"] is False
    assert protected_question["answer_options"]
    assert "never cleared in bulk" in protected_question["how_your_answer_is_used"]
    assert built["summary"]["batch_clearing_question_count"] == 1
    assert built["summary"]["individually_rechecked_question_count"] == 1
    assert built["summary"]["coverage_pct"] == 100.0

    # A wrapper group is the one thing with nothing to ask.
    wrapper = questions.build_questions(
        [dict(groups[0], finding_family="document_wrapper", protected=False)], items
    )["questions"][0]
    assert wrapper["client_answer_permitted"] is False
    assert wrapper["answer_options"] == []

    # Truncation shortens the ask, never the queue.
    capped = questions.build_questions(groups, items, max_questions=1)
    assert capped["summary"]["question_count"] == 1
    assert capped["summary"]["deferred_question_count"] == 1
    assert capped["summary"]["queue_item_count"] == 6
    with pytest.raises(ValueError, match="positive integer"):
        questions.build_questions(groups, items, max_questions=0)

    assert questions.template_for("unrecognised") is questions.DEFAULT_TEMPLATE
    assert questions.group_priority({}, [{"priority": "critical"}]) == "critical"
    assert questions.example_for([])["document_id"] == ""


def test_evidence_attaches_real_pages_and_reports_every_failure(tmp_path):
    """A page that cannot be produced becomes an exception, never a substitute."""
    manifest = manifest_file(tmp_path, ["p1"], real_pdf=True)
    index = evidence.page_index(manifest)
    assert index["p1"].is_file()

    def fake_render(page_path, image_path, dpi):
        Path(image_path).parent.mkdir(parents=True, exist_ok=True)
        Path(image_path).write_bytes(JPEG)
        return Path(image_path)

    asked = [
        {"question_id": "Q001", "example": {"page_id": "p1", "document_id": "d"}},
        {"question_id": "Q002", "example": {"page_id": "p1", "document_id": "d"}},
        {"question_id": "Q003", "example": {"page_id": "missing", "document_id": "d"}},
    ]
    failures = evidence.attach_examples(asked, manifest, tmp_path / "img", renderer=fake_render)
    assert asked[0]["example"]["page_image_status"] == "rendered"
    # The second question reuses the first render rather than paying for it twice.
    assert asked[1]["example"]["page_image"] == asked[0]["example"]["page_image"]
    assert asked[2]["example"]["page_image"] is None
    assert len(failures) == 1 and "not found" in failures[0]["reason"]

    # An oversized render is retained as an exception, not silently shrunk.
    over = [{"question_id": "Q004", "example": {"page_id": "p1", "document_id": "d"}}]
    bounded = evidence.attach_examples(
        over, manifest, tmp_path / "img2", max_image_bytes=1, renderer=fake_render
    )
    assert over[0]["example"]["page_image_status"] == "render_failed"
    assert "above the 1-byte bound" in bounded[0]["reason"]

    # Without a manifest the questions simply carry no example.
    none_asked = [{"question_id": "Q005", "example": {"page_id": "p1"}}]
    assert evidence.attach_examples(none_asked, None, tmp_path / "img3") == []
    assert none_asked[0]["example"]["page_image_status"] == "not_requested"

    for payload, message in (
        ({"pages": []}, "non-empty pages list"),
        ({"pages": [{"page_id": 1}]}, "no page_id to page_pdf mapping"),
    ):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match=message):
            evidence.page_index(bad)


def test_evidence_render_uses_poppler_and_fails_closed(tmp_path):
    """The renderer reports a poppler failure rather than returning nothing."""
    calls = {}

    def failing(argv, **kwargs):
        calls["argv"] = argv
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    with pytest.raises(ValueError, match="pdftoppm failed: boom"):
        evidence.render_page(tmp_path / "p.pdf", tmp_path / "p.jpg", 150, runner=failing)
    assert "-jpeg" in calls["argv"]

    def silent(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(ValueError, match="did not produce"):
        evidence.render_page(tmp_path / "p.pdf", tmp_path / "p.jpg", 150, runner=silent)


def test_pdf_measures_text_and_gives_evidence_its_own_sheet(tmp_path):
    """The PDF wraps from real widths and turns the sheet for a landscape scan."""
    assert instructions.text_width("AAA", 10) > instructions.text_width("iii", 10)
    assert instructions.wrap("", 10, 100) == [""]
    # A word wider than the column is kept whole rather than broken.
    assert instructions.wrap("supercalifragilistic", 10, 5) == ["supercalifragilistic"]
    assert instructions.escape("a(b)c\\d") == rb"a\(b\)c\\d"
    # Characters Helvetica cannot show are replaced, never crash the render.
    assert instructions.escape("café 中")

    image = tmp_path / "page.jpg"
    image.write_bytes(JPEG)
    payload = {
        "summary": {"answerable_question_count": 1, "queue_item_count": 3, "items_covered": 3},
        "questions": [
            {
                "question_id": "Q001",
                "headline": "Which reading is right?",
                "what_we_found": "They disagreed.",
                "why_it_matters": "We do not guess.",
                "what_we_need": "Confirm the value.",
                "how_your_answer_is_used": "Applied then re-checked.",
                "answer_options": ["First", "Second"],
                "resolves_item_count": 3,
                "document_count": 2,
                "protected": True,
                "client_answer_permitted": True,
                "batch_clear_permitted": False,
                "example": {"document_id": "d1", "field": "total", "page_image": str(image)},
            },
            {
                "question_id": "Q002",
                "headline": "No page for this one.",
                "what_we_found": "x",
                "why_it_matters": "y",
                "what_we_need": "z",
                "how_your_answer_is_used": "Settles the group.",
                "answer_options": [],
                "resolves_item_count": 1,
                "document_count": 1,
                "protected": False,
                "client_answer_permitted": True,
                "batch_clear_permitted": True,
                "example": {"document_id": "", "field": "", "page_image": None},
            },
        ],
    }
    output = instructions.write_instructions_pdf(payload, tmp_path / "guide.pdf")
    assert output.is_file()
    body = output.read_bytes()
    assert b"/DCTDecode" in body and b"Helvetica-Bold" in body
    with pytest.raises(FileExistsError, match="overwrite"):
        instructions.write_instructions_pdf(payload, output)

    # The sample JPEG is 1x1, so its evidence sheet stays portrait.
    assert instructions.example_page(payload["questions"][1]) is None
    sheet = instructions.example_page(payload["questions"][0])
    assert sheet is not None and sheet.image is not None

    # With no room left, an image is skipped rather than drawn off the page.
    crowded = instructions.Page()
    crowded.cursor = 60
    crowded.place_image(image, "")
    assert crowded.image is None


def test_workbook_carries_dropdowns_and_the_whole_queue(tmp_path):
    """The answering surface is short; the exhaustive queue is still present."""
    payload = {
        "summary": {"answerable_question_count": 1, "queue_item_count": 2, "items_covered": 2},
        "questions": [
            {
                "question_id": "Q001",
                "headline": "Which reading is right?",
                "how_your_answer_is_used": "Applied then re-checked.",
                "answer_options": ["First", "Second"],
                "resolves_item_count": 2,
                "document_count": 1,
                "priority": "high",
                "client_answer_permitted": True,
                "example": {"document_id": "d1", "field": "total"},
            },
            {
                "question_id": "Q002",
                "headline": "Held open elsewhere.",
                "how_your_answer_is_used": "",
                "answer_options": [],
                "resolves_item_count": 1,
                "document_count": 1,
                "priority": "normal",
                "client_answer_permitted": False,
                "example": {},
            },
        ],
    }
    items = [consensus_item(0), consensus_item(1)]
    output = workbook.write_workbook(payload, items, tmp_path / "review.xlsx")
    assert output.is_file() and output.stat().st_size > 0
    with pytest.raises(FileExistsError, match="overwrite"):
        workbook.write_workbook(payload, items, output)


def test_lane_builds_one_pack_from_any_blocked_control(tmp_path, monkeypatch):
    """The same three deliverables come out regardless of which control blocked."""
    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(i) for i in range(4)]
    )
    manifest = manifest_file(tmp_path, [f"doc-{i:03d}" for i in range(4)], real_pdf=True)

    def fake_render(page_path, image_path, dpi):
        Path(image_path).parent.mkdir(parents=True, exist_ok=True)
        Path(image_path).write_bytes(JPEG)
        return Path(image_path)

    pack = lane.build_pack(
        [artifact],
        tmp_path / "pack",
        manifest=manifest,
        renderer=fake_render,
    )
    out = tmp_path / "pack"
    assert (out / "client_review.xlsx").is_file()
    assert (out / "client_review_instructions.pdf").is_file()
    assert (out / "review_pack.json").is_file()
    assert (out / "review_pack_exceptions.json").is_file()
    assert pack["trigger"]["triggered_blocks"] == ["consensus"]
    assert pack["summary"]["queue_item_count"] == 4
    assert pack["exception_count"] == 0

    with pytest.raises(FileExistsError, match="overwrite"):
        lane.build_pack([artifact], out)
    with pytest.raises(ValueError, match="at least one source artifact"):
        lane.build_pack([], tmp_path / "empty")
    # Rule 9: a pack built from nothing would report a review nobody performed.
    nothing = exceptions_file(tmp_path, "empty_exceptions.json", [])
    with pytest.raises(ValueError, match="a pack built from nothing"):
        lane.build_pack([nothing], tmp_path / "none")


def test_lane_stays_quiet_below_threshold_but_never_drops_the_queue(tmp_path, monkeypatch):
    """Below every floor there is nothing to ask, and the items are still there."""
    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(i) for i in range(2)]
    )
    monkeypatch.setenv("CLIENT_REVIEW_LANE_CONSENSUS_THRESHOLD", "50")
    pack = lane.build_pack([artifact], tmp_path / "quiet")
    assert pack["trigger"]["produced_documents"] is False
    assert pack["summary"]["queue_item_count"] == 2
    assert "threshold" in pack["summary"]["reason"]
    assert not (tmp_path / "quiet" / "client_review.xlsx").exists()

    monkeypatch.setenv("CLIENT_REVIEW_LANE_ENABLED", "false")
    disabled = lane.build_pack([artifact], tmp_path / "off")
    assert "disabled" in disabled["summary"]["reason"]

    # Forcing is recorded, and never lowers a threshold.
    forced = lane.build_pack([artifact], tmp_path / "forced", force=True)
    assert forced["trigger"]["forced"] is True
    assert forced["trigger"]["triggered"] is False
    assert (tmp_path / "forced" / "client_review.xlsx").is_file()


def test_lane_labels_families_from_consensus_when_it_is_supplied(tmp_path):
    """Consensus sharpens the grouping; without it the groups are merely broader."""
    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(i) for i in range(2)]
    )
    consensus = tmp_path / "consensus.json"
    consensus.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "document_id": "doc-000",
                        "fields": {"document_type": {"value": "commission_statement"}},
                    }
                ]
            }
        )
    )
    labelled = lane.build_pack([artifact], tmp_path / "labelled", consensus=consensus)
    families = {group["template_family"] for group in labelled["groups"]}
    assert "commission_statement" in families

    not_an_object = tmp_path / "list.json"
    not_an_object.write_text(json.dumps(["nope"]))
    fallback = lane.build_pack([artifact], tmp_path / "fallback", consensus=not_an_object)
    assert fallback["groups"]


def test_lane_cli_reports_what_it_asked_for(tmp_path, monkeypatch, capsys):
    """The command says what it produced, or why it produced nothing."""
    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(i) for i in range(3)]
    )
    argv = [
        "client_review_lane.py",
        "build",
        str(artifact),
        "--out-dir",
        str(tmp_path / "cli"),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 0
    printed = capsys.readouterr().out
    assert "Questions:" in printed and "triggered by: consensus" in printed

    monkeypatch.setenv("CLIENT_REVIEW_LANE_CONSENSUS_THRESHOLD", "99")
    monkeypatch.setattr(sys, "argv", [*argv[:-1], str(tmp_path / "cli2")])
    assert cli.main() == 0
    assert "No pack produced" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", [*argv[:-1], str(tmp_path / "cli3"), "--quiet"])
    assert cli.main() == 0
    assert capsys.readouterr().out == ""

    # An existing output directory is refused rather than overwritten.
    monkeypatch.delenv("CLIENT_REVIEW_LANE_CONSENSUS_THRESHOLD", raising=False)
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="Client review lane failed"):
        cli.main()


def test_remaining_boundaries(tmp_path):
    """Cover the guards that only fire on malformed or repeated input."""
    # A manifest entry that is not an object is skipped, not fatal.
    mixed = tmp_path / "mixed.json"
    mixed.write_text(
        json.dumps({"pages": ["not-an-object", {"page_id": "p1", "page_pdf": "pages/p1.pdf"}]})
    )
    assert list(evidence.page_index(mixed)) == ["p1"]

    # A successful render returns the retained image.
    def writing(argv, **kwargs):
        Path(argv[-1] + ".jpg").write_bytes(JPEG)
        return subprocess.CompletedProcess(argv, 0, "", "")

    produced = evidence.render_page(tmp_path / "p.pdf", tmp_path / "out.jpg", 150, runner=writing)
    assert produced.read_bytes() == JPEG

    # A second pack reuses an image already on disk rather than re-rendering.
    manifest = manifest_file(tmp_path, ["p1"], real_pdf=True)
    images = tmp_path / "shared"
    images.mkdir()
    (images / "p1.jpg").write_bytes(JPEG)

    def refuse(page_path, image_path, dpi):  # pragma: no cover - must not run
        raise AssertionError("an existing render must not be rebuilt")

    asked = [{"question_id": "Q001", "example": {"page_id": "p1", "document_id": "d"}}]
    assert evidence.attach_examples(asked, manifest, images, renderer=refuse) == []
    assert asked[0]["example"]["page_image_status"] == "rendered"

    # A captioned image draws its caption; an example with no identity omits one.
    image = tmp_path / "cap.jpg"
    image.write_bytes(JPEG)
    captioned = instructions.Page()
    captioned.place_image(image, "The page this came from:")
    assert b"The page this came from" in captioned.stream()

    anonymous = instructions.example_page(
        {
            "question_id": "Q009",
            "example": {"document_id": "", "field": "", "page_image": str(image)},
        }
    )
    assert anonymous is not None and anonymous.image is not None

    # A non-blocking observation is not counted against any threshold.
    assert (
        thresholds.evaluate([{"blocking": False, "reason": "partial_disagreement"}])[
            "blocking_item_count"
        ]
        == 0
    )


def test_documented_entry_points_still_resolve():
    """The published command paths keep working after the move into the package.

    Every one of these is named in the command-line reference and in operator
    runbooks. Moving their logic into :mod:`client_review` must not silently
    break the paths people already run.
    """
    for module_name, target in (
        ("review_grouping", "client_review.grouping"),
        ("final_review_queue", "client_review.queue"),
        ("client_review_llm", "client_review.llm"),
        ("client_review_context", "client_review.context"),
        ("client_review_inference", "client_review.inference"),
        ("client_review_iterative", "client_review.iterative"),
        ("client_review_consensus", "client_review.consensus_review"),
        ("client_review_exception_resolution", "client_review.exception_resolution"),
        ("client_review_cross_packet", "client_review.cross_packet"),
        ("client_review_cross_record", "client_review.cross_record"),
        ("ai_simulated_client_review", "client_review.simulated"),
        ("safe_review_consolidation", "client_review.consolidation"),
        ("client_review_package", "client_review.package"),
        ("review_agent", "client_review.agent"),
    ):
        shim = importlib.import_module(module_name)
        assert shim.main is importlib.import_module(target).main


def test_queue_keeps_the_control_a_lane_named(tmp_path):
    """A lane's own review_source survives consolidation.

    The queue used to overwrite it with the collection name, so a finding from
    the slot-equivalence lane arrived labelled ``exceptions`` and fell through
    per-control thresholds as unclassified.
    """
    artifact = exceptions_file(
        tmp_path,
        "exceptions.json",
        [
            {
                "document_id": "d1",
                "page_id": "d1",
                "field": "item_code|job_number",
                "reason": "Independent verifier returned conflict",
                "review_source": "slot_equivalence",
            }
        ],
    )
    pack = lane.build_pack([artifact], tmp_path / "pack")
    assert pack["queue_items"][0]["review_source"] == "slot_equivalence"
    assert pack["trigger"]["triggered_blocks"] == ["slot_equivalence"]


def test_every_question_template_is_client_readable_prose():
    """Each template field must be a string, not a container holding one.

    A stray trailing comma made one ``why_it_matters`` a single-element tuple,
    which rendered as ``('Picking one...',)`` in a client-facing PDF. Line
    coverage cannot catch that -- the dict literal executes either way -- so the
    shape is asserted directly, for every family rather than the few a given
    corpus happens to produce.
    """
    templates = [*questions.QUESTION_TEMPLATES.values(), questions.DEFAULT_TEMPLATE]
    for template in templates:
        for field in ("headline", "what_we_found", "why_it_matters", "what_we_need"):
            value = template[field]
            assert isinstance(value, str), f"{field} must be prose, got {type(value).__name__}"
            assert value == value.strip() and value
        assert isinstance(template["answer_options"], tuple)
        for option in template["answer_options"]:
            assert isinstance(option, str) and option


def test_final_queue_refuses_to_clear_a_gate_over_nothing(tmp_path):
    """Rule 9 on the control where it matters most.

    Fed an artifact carrying no review-bearing content, the final queue used to
    report ``gate_status: clear``. An operator who points it at the wrong files,
    or at a stage that never ran, would read that as authorization.
    """
    from client_review import queue

    irrelevant = tmp_path / "irrelevant.json"
    irrelevant.write_text(json.dumps({"unrelated": "carries no review collection"}))
    with pytest.raises(ValueError, match="cannot report a clear gate over nothing"):
        queue.consolidate([irrelevant])
    # The refusal names the file, so the operator knows which one to replace.
    with pytest.raises(ValueError, match="irrelevant.json"):
        queue.consolidate([irrelevant])

    # A genuinely empty exception list is a real clean result and still passes.
    empty = exceptions_file(tmp_path, "consensus_exceptions.json", [])
    items, sources, _ = queue.consolidate([empty])
    assert items == [] and sources == ["consensus_exceptions.json"]

    # One review-bearing artifact is enough; a manifest alongside it is fine.
    manifest = tmp_path / "run_manifest.json"
    manifest.write_text(json.dumps({"artifacts": [], "pipeline_version": "1.0.0"}))
    items, sources, _ = queue.consolidate([manifest, empty])
    assert sorted(sources) == ["consensus_exceptions.json", "run_manifest.json"]

    assert queue.review_bearing({"gate_status": "blocked"})
    assert queue.review_bearing({"artifact_type": "table_comprehension_quality_summary"})
    assert not queue.review_bearing(["not-an-object"])

    # The lane inherits the refusal rather than writing an empty pack.
    with pytest.raises(ValueError, match="cannot report a clear gate over nothing"):
        lane.build_pack([irrelevant], tmp_path / "pack")


def _set_cell(path: Path, sheet_part: str, ref: str, text: str) -> None:
    """Write an inline string into one cell, as some spreadsheets do on save."""
    import re
    import zipfile

    with zipfile.ZipFile(path) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}
    xml = items[sheet_part].decode()
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    cell = f'<c r="{ref}" s="4" t="inlineStr"><is><t>{escaped}</t></is></c>'
    pattern = re.compile(rf'<c r="{ref}"(?:[^>]*?/>|[^>]*>.*?</c>)', re.S)
    assert pattern.search(xml), f"cell {ref} not present"
    items[sheet_part] = pattern.sub(cell, xml, count=1).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in items.items():
            archive.writestr(name, data)


def _read_sheet_cells(path: Path, sheet_part: str) -> dict[str, str]:
    """Read one worksheet back as {cell reference: text}, shared strings resolved."""
    import xml.etree.ElementTree as ET
    import zipfile

    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            for entry in ET.fromstring(archive.read("xl/sharedStrings.xml")):  # noqa: S314 - this test wrote the workbook it is reading moments earlier
                shared.append("".join(node.text or "" for node in entry.iter(f"{{{ns['m']}}}t")))
        sheet = ET.fromstring(archive.read(sheet_part))  # noqa: S314 - this test wrote the workbook it is reading moments earlier
    cells = {}
    for cell in sheet.iter(f"{{{ns['m']}}}c"):
        ref = cell.get("r")
        inline = cell.find("m:is/m:t", ns)
        if inline is not None:
            cells[ref] = inline.text or ""
            continue
        value = cell.find("m:v", ns)
        if value is None or value.text is None:
            continue
        cells[ref] = (
            shared[int(value.text)] if cell.get("t") == "s" and value.text.isdigit() else value.text
        )
    return cells


def test_a_returned_workbook_round_trips_back_into_answers(tmp_path):
    """The pack asks questions; this is the half that receives the replies.

    The instruction PDF tells a client to send the workbook back, so the loop has
    to close. Everything about the returned file is untrusted: it is validated
    against the immutable issued copy before an answer is read.
    """
    import shutil

    from client_review import answers as answer_module

    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(index) for index in range(4)]
    )
    pack = lane.build_pack([artifact], tmp_path / "pack")
    issued = tmp_path / "pack" / "client_review.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copy(issued, returned)

    # Row 3 is the header, so the first question sits on row 4.
    _set_cell(returned, "xl/worksheets/sheet2.xml", "D4", "The value shown is correct")
    _set_cell(returned, "xl/worksheets/sheet2.xml", "E4", "Julie confirmed by email")

    result = answer_module.write_answers(
        returned, issued, tmp_path / "pack" / "review_pack.json", tmp_path / "answers.json"
    )
    assert result["summary"]["answered"] == 1
    answer = result["answers"][0]
    assert answer["answer"] == "The value shown is correct"
    assert answer["note"] == "Julie confirmed by email"
    assert answer["matched_offered_option"] is True
    # Rule 7: a consensus disagreement is informed by the answer, never closed.
    assert answer["application"] == "informs_item_by_item_recheck"
    assert result["policy"]["applied"] is False
    assert result["summary"]["unanswered"] == len(pack["questions"]) - 1

    with pytest.raises(FileExistsError, match="overwrite"):
        answer_module.write_answers(
            returned, issued, tmp_path / "pack" / "review_pack.json", tmp_path / "answers.json"
        )


def test_a_returned_workbook_is_untrusted_until_it_matches_the_issued_copy(tmp_path):
    """Any edit outside the two answer columns refuses the whole file."""
    import shutil

    from client_review import answers as answer_module

    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(index) for index in range(4)]
    )
    lane.build_pack([artifact], tmp_path / "pack")
    issued = tmp_path / "pack" / "client_review.xlsx"
    pack_path = tmp_path / "pack" / "review_pack.json"

    tampered = tmp_path / "tampered.xlsx"
    shutil.copy(issued, tampered)
    # Column E is "How your answer is used" -- fixed, and not the client's to edit.
    _set_cell(tampered, "xl/worksheets/sheet2.xml", "F4", "this was changed")
    with pytest.raises(ValueError, match="changed a fixed cell"):
        answer_module.write_answers(tampered, issued, pack_path, tmp_path / "a.json")

    renamed = tmp_path / "renamed.xlsx"
    shutil.copy(issued, renamed)
    _set_cell(renamed, "xl/worksheets/sheet2.xml", "A4", "Q999")
    with pytest.raises(ValueError, match="reordered or renamed"):
        answer_module.write_answers(renamed, issued, pack_path, tmp_path / "b.json")

    with pytest.raises(ValueError, match="separate from the immutable issued"):
        answer_module.write_answers(issued, issued, pack_path, tmp_path / "c.json")

    not_a_pack = tmp_path / "wrong.json"
    not_a_pack.write_text(json.dumps({"schema_version": "something_else"}))
    returned = tmp_path / "returned2.xlsx"
    shutil.copy(issued, returned)
    with pytest.raises(ValueError, match="must be the review_pack.json"):
        answer_module.write_answers(returned, issued, not_a_pack, tmp_path / "d.json")


def test_an_unanswered_or_unmatched_reply_is_reported_not_guessed(tmp_path):
    """An answer outside the offered options is the client's, and is kept."""
    import shutil

    from client_review import answers as answer_module

    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(index) for index in range(4)]
    )
    lane.build_pack([artifact], tmp_path / "pack")
    issued = tmp_path / "pack" / "client_review.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copy(issued, returned)
    _set_cell(returned, "xl/worksheets/sheet2.xml", "D4", "none of these fit")

    result = answer_module.write_answers(
        returned, issued, tmp_path / "pack" / "review_pack.json", tmp_path / "answers.json"
    )
    assert result["summary"]["answers_not_matching_an_offered_option"] == 1
    assert result["answers"][0]["answer"] == "none of these fit"
    assert result["answers"][0]["matched_offered_option"] is False


def test_read_answers_cli_reports_what_came_back(tmp_path, monkeypatch, capsys):
    """The command says what was answered and that nothing was applied."""
    import shutil

    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(index) for index in range(4)]
    )
    lane.build_pack([artifact], tmp_path / "pack")
    issued = tmp_path / "pack" / "client_review.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copy(issued, returned)
    _set_cell(returned, "xl/worksheets/sheet2.xml", "D4", "not an offered option")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_lane.py",
            "read-answers",
            str(returned),
            "--issued-workbook",
            str(issued),
            "--pack",
            str(tmp_path / "pack" / "review_pack.json"),
            "--out",
            str(tmp_path / "answers.json"),
        ],
    )
    assert cli.main() == 0
    printed = capsys.readouterr().out
    assert "did not match an offered option" in printed
    assert "nothing has been applied" in printed

    monkeypatch.setattr(sys, "argv", ["client_review_lane.py"])
    with pytest.raises(SystemExit):
        cli.main()


def test_answer_reader_refuses_every_shape_it_cannot_trust(tmp_path):
    """The remaining refusals: a missing sheet, a changed header, a short file."""
    import shutil

    from client_review import answers as answer_module

    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(index) for index in range(4)]
    )
    pack = lane.build_pack([artifact], tmp_path / "pack")
    issued = tmp_path / "pack" / "client_review.xlsx"
    pack_path = tmp_path / "pack" / "review_pack.json"

    # A header cell renamed changes the whole sheet's meaning.
    header_changed = tmp_path / "header.xlsx"
    shutil.copy(issued, header_changed)
    _set_cell(header_changed, "xl/worksheets/sheet2.xml", "D3", "Answer")
    with pytest.raises(ValueError, match="changed the Questions header"):
        answer_module.write_answers(header_changed, issued, pack_path, tmp_path / "a.json")

    # A row deleted leaves fewer questions than were issued.
    import re
    import zipfile

    short = tmp_path / "short.xlsx"
    with zipfile.ZipFile(issued) as archive:
        items = {name: archive.read(name) for name in archive.namelist()}
    xml = items["xl/worksheets/sheet2.xml"].decode()
    # Row 4 is the first question row; these four findings group into one.
    updated = re.sub(r'<row r="4".*?</row>', "", xml, count=1, flags=re.S)
    assert updated != xml
    items["xl/worksheets/sheet2.xml"] = updated.encode()
    with zipfile.ZipFile(short, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in items.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="different number of question rows"):
        answer_module.write_answers(short, issued, pack_path, tmp_path / "b.json")

    # A pack that does not describe this workbook's questions.
    other_pack = tmp_path / "other_pack.json"
    other_pack.write_text(json.dumps({"schema_version": "client_review_pack_v1", "questions": []}))
    returned = tmp_path / "returned.xlsx"
    shutil.copy(issued, returned)
    with pytest.raises(ValueError, match="absent from the pack"):
        answer_module.write_answers(returned, issued, other_pack, tmp_path / "c.json")

    assert pack["questions"]


def test_read_answers_cli_reports_a_refusal(tmp_path, monkeypatch):
    """A refused workbook exits with the reason rather than a traceback."""
    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(index) for index in range(4)]
    )
    lane.build_pack([artifact], tmp_path / "pack")
    issued = tmp_path / "pack" / "client_review.xlsx"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_lane.py",
            "read-answers",
            str(issued),
            "--issued-workbook",
            str(issued),
            "--pack",
            str(tmp_path / "pack" / "review_pack.json"),
            "--out",
            str(tmp_path / "answers.json"),
        ],
    )
    with pytest.raises(SystemExit, match="Client review lane failed"):
        cli.main()


def test_answer_reader_handles_a_quiet_untouched_and_headerless_workbook(
    tmp_path, monkeypatch, capsys
):
    """An untouched return, a quiet run, and a sheet with no header row."""
    import shutil

    from client_review import answers as answer_module

    artifact = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(index) for index in range(4)]
    )
    lane.build_pack([artifact], tmp_path / "pack")
    issued = tmp_path / "pack" / "client_review.xlsx"
    pack_path = tmp_path / "pack" / "review_pack.json"

    # A client who returns the workbook without filling anything in.
    untouched = tmp_path / "untouched.xlsx"
    shutil.copy(issued, untouched)
    result = answer_module.write_answers(untouched, issued, pack_path, tmp_path / "none.json")
    assert result["summary"]["answered"] == 0
    assert result["unanswered_questions"]

    # An answered return, read quietly, with every answer matching an option.
    answered = tmp_path / "answered.xlsx"
    shutil.copy(issued, answered)
    _set_cell(answered, "xl/worksheets/sheet2.xml", "D4", "The value shown is correct")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_lane.py",
            "read-answers",
            str(answered),
            "--issued-workbook",
            str(issued),
            "--pack",
            str(pack_path),
            "--out",
            str(tmp_path / "quiet.json"),
            "--quiet",
        ],
    )
    assert cli.main() == 0
    assert capsys.readouterr().out == ""

    # The same answer read aloud: every answer matched an option, so the
    # unmatched line is not printed.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_lane.py",
            "read-answers",
            str(answered),
            "--issued-workbook",
            str(issued),
            "--pack",
            str(pack_path),
            "--out",
            str(tmp_path / "loud.json"),
        ],
    )
    assert cli.main() == 0
    printed = capsys.readouterr().out
    assert "did not match an offered option" not in printed
    assert "nothing has been applied" in printed

    # A sheet whose header row was removed entirely.
    headerless = tmp_path / "headerless.xlsx"
    shutil.copy(issued, headerless)
    _set_cell(headerless, "xl/worksheets/sheet2.xml", "A3", "not the header")
    with pytest.raises(ValueError, match="no Questions header row"):
        answer_module.write_answers(headerless, issued, pack_path, tmp_path / "h.json")


def test_a_provider_lane_exception_is_named_by_its_lane():
    """Provider exceptions arrive with a generic collection name.

    On a real run, 115 findings from primary_exceptions.json and
    secondary_exceptions.json classified as unclassified. They still triggered,
    but the report told an operator nothing about where they came from.
    """
    assert thresholds.classify({"source_artifact": "primary_exceptions.json"}) == "extraction"
    assert thresholds.classify({"source_artifact": "secondary_exceptions.json"}) == "extraction"
    # Consensus still wins on its own vocabulary rather than the artifact name.
    assert (
        thresholds.classify(
            {"source_artifact": "consensus_exceptions.json", "reason": "partial_disagreement"}
        )
        == "consensus"
    )


def test_a_prefilled_cell_is_not_a_client_answer(tmp_path):
    """An answer is what the client changed, not what the workbook pre-filled.

    The workbook writes "No answer needed" into the answer cell of a question
    that needs no reply. Reading any non-empty cell as a reply turned that
    placeholder into a decision the client never made -- found by running
    read-answers against a real pack and getting two answers from one filled row.
    """
    import shutil

    from client_review import answers as answer_module

    # A wrapper question needs no reply, so its answer cell arrives pre-filled.
    artifact = exceptions_file(
        tmp_path,
        "consensus_exceptions.json",
        [
            consensus_item(0),
            {
                "document_id": "doc-001",
                "page_id": "doc-001",
                "field": "page",
                "reason": "document_status_open_exception",
                "review_source": "documents",
            },
        ],
    )
    pack = lane.build_pack([artifact], tmp_path / "pack")
    assert any(not q["client_answer_permitted"] for q in pack["questions"])

    issued = tmp_path / "pack" / "client_review.xlsx"
    returned = tmp_path / "returned.xlsx"
    shutil.copy(issued, returned)

    # Return it completely untouched: nothing was answered.
    result = answer_module.write_answers(
        returned, issued, tmp_path / "pack" / "review_pack.json", tmp_path / "none.json"
    )
    assert result["summary"]["answered"] == 0
    assert result["summary"]["unanswered"] == len(pack["questions"])


def test_a_single_reading_is_not_asked_as_a_disagreement(tmp_path):
    """Only one engine read the field, so there is no second value to choose.

    Both causes once collapsed into one `provider_disagreement` question, which
    asked 52,528 single-reading items "which of the two readings is right?" --
    a question with no answer, offering options naming a value that was never
    read.
    """
    from client_review import grouping

    assert grouping.disagreement_family("partial_disagreement") == "single_reading"
    assert grouping.disagreement_family("no_majority") == "provider_disagreement"

    artifact = exceptions_file(
        tmp_path,
        "consensus_exceptions.json",
        [consensus_item(i, reason="partial_disagreement") for i in range(3)]
        + [consensus_item(i + 10, reason="no_majority") for i in range(3)],
    )
    pack = lane.build_pack([artifact], tmp_path / "pack")
    families = {q["finding_family"] for q in pack["questions"]}
    assert {"single_reading", "provider_disagreement"} <= families

    single = next(q for q in pack["questions"] if q["finding_family"] == "single_reading")
    assert "Only one of our two systems" in single["headline"]
    # The options must not offer a value the client was never shown.
    assert not any("second" in option.casefold() for option in single["answer_options"])
    assert "The value shown is correct" in single["answer_options"]

    both = next(q for q in pack["questions"] if q["finding_family"] == "provider_disagreement")
    assert any("first value shown" in option for option in both["answer_options"])


def test_a_worked_example_shows_the_values_that_were_read(tmp_path):
    """A client asked which reading is right must be shown the readings."""
    from client_review import questions

    example = questions.example_for(
        [
            {
                "document_id": "doc-1",
                "page_id": "doc-1",
                "field": "header.brand_name",
                "reason": "no_majority",
                "candidates": ["murb rook", "murbrook"],
            }
        ]
    )
    assert example["values_read"] == ["murb rook", "murbrook"]
    # An item that retained no candidates still produces a usable example.
    assert questions.example_for([{"document_id": "d", "field": "f"}])["values_read"] == []


def test_a_payment_export_without_document_keys_gets_its_own_question(tmp_path):
    """814 payment rows named no document, and the pack asked about them in raw
    reason-token wording because the cause had no family of its own.

    The finding is real -- completeness retains every unusable row with its
    reason -- and the question it deserves is what one payment represents, which
    only the client can answer.
    """
    from client_review import grouping, questions

    assert grouping.disagreement_family("payment_row_has_no_invoice_number") == (
        "payment_settlement_grain"
    )
    template = questions.QUESTION_TEMPLATES["payment_settlement_grain"]
    # The answer that resolves the whole group must be offered, not just described.
    assert any("periodic settlement" in option for option in template["answer_options"])
    assert any(
        "export payments with the document" in option for option in template["answer_options"]
    )
    # It must not assert a shortfall: an unpaid-looking document may be in a paid batch.
    assert "batch" in template["why_it_matters"]

    artifact = exceptions_file(
        tmp_path,
        "completeness_exceptions.json",
        [consensus_item(i, reason="payment_row_has_no_invoice_number") for i in range(3)],
    )
    pack = lane.build_pack([artifact], tmp_path / "pack")
    question = next(
        q for q in pack["questions"] if q["finding_family"] == "payment_settlement_grain"
    )
    assert "What does one payment cover?" in question["headline"]


def test_a_question_a_control_already_answered_is_reconciled_not_asked_again(tmp_path):
    """The gate is handed both the raw proposals and the control that resolved them.

    On the production run it carried 1,382 raw ``document_type`` proposals from
    the two adapters while ``classification_consensus_04`` had already accepted
    686 of the 716 documents. Nothing reconciled the two, so the client was
    asked 1,364 questions the run had answered itself.
    """
    from client_review import queue

    proposals = exceptions_file(
        tmp_path,
        "primary_exceptions.json",
        [
            {
                "document_id": "doc-001",
                "page_id": "doc-001",
                "field": "document_type",
                "candidate_value": "commission_statement",
                "reason": "openai_document_type_proposal",
            },
            {
                "document_id": "doc-002",
                "page_id": "doc-002",
                "field": "document_type",
                "candidate_value": "invoice",
                "reason": "openai_document_type_proposal",
            },
            {
                "document_id": "doc-003",
                "page_id": "doc-003",
                "field": "document_type",
                "candidate_value": "invoice",
                "reason": "openai_document_type_proposal",
            },
            consensus_item(4),
        ],
    )
    resolved = tmp_path / "classification_consensus.json"
    resolved.write_text(
        json.dumps(
            {
                "artifact_type": "classification_consensus_v1",
                "accepted": [
                    {
                        "document_id": "doc-001",
                        "document_type": "commission_statement",
                        "evidence": "independent_model_vendor_agreement",
                    },
                    # Accepted as something else: the proposal disagrees with a
                    # resolved control, which is a finding, not noise.
                    {
                        "document_id": "doc-002",
                        "document_type": "commission_statement",
                        "evidence": "independent_model_vendor_agreement",
                    },
                    {"document_id": "doc-009", "document_type": "invoice"},
                ],
            }
        )
    )

    accepted = queue.resolutions([resolved])
    items, _, reconciled = queue.consolidate([proposals], accepted)
    reasons = sorted(item["reason"] for item in items)

    # doc-001 matched the accepted type and is retained, not closed.
    assert [entry["document_id"] for entry in reconciled] == ["doc-001"]
    assert reconciled[0]["disposition"] == "already_resolved"
    assert reconciled[0]["resolved_by"] == "classification_consensus.json"
    assert reconciled[0]["evidence"] == "independent_model_vendor_agreement"
    # doc-002 named a different type and doc-003 was never accepted: both stay.
    assert reasons == ["openai_document_type_proposal"] * 2 + ["partial_disagreement"]
    assert {item["document_id"] for item in items} == {"doc-002", "doc-003", "doc-004"}

    # A finding that is not about the document type is never reconciled by this.
    assert (
        queue.reconciled_by({"field": "lines[0].amount", "document_id": "doc-001"}, accepted)
        is None
    )

    # Rule 9: a reconciliation that reconciled nothing has not run.
    empty = tmp_path / "empty_consensus.json"
    empty.write_text(json.dumps({"artifact_type": "classification_consensus_v1", "accepted": []}))
    with pytest.raises(ValueError, match="reconciliation that processed nothing"):
        queue.resolutions([empty])
    with pytest.raises(ValueError, match="not a resolution artifact"):
        queue.resolutions([proposals])
    # An entry with no document id cannot resolve anything and is skipped.
    partial = tmp_path / "partial_consensus.json"
    partial.write_text(
        json.dumps(
            {
                "artifact_type": "classification_consensus_v1",
                "accepted": [{"document_type": "invoice"}, {"document_id": "doc-001"}],
            }
        )
    )
    assert list(queue.resolutions([partial])) == ["doc-001"]


def test_the_queue_records_which_control_answered_what(tmp_path, capsys, monkeypatch):
    """A reconciled item is auditable from the queue artifact itself."""
    from client_review import queue

    proposals = exceptions_file(
        tmp_path,
        "primary_exceptions.json",
        [
            {
                "document_id": "doc-001",
                "page_id": "doc-001",
                "field": "document_type",
                "candidate_value": "invoice",
                "reason": "openai_document_type_proposal",
            },
            consensus_item(2),
        ],
    )
    resolved = tmp_path / "classification_consensus.json"
    resolved.write_text(
        json.dumps(
            {
                "artifact_type": "classification_consensus_v1",
                "accepted": [{"document_id": "doc-001", "document_type": "invoice"}],
            }
        )
    )
    out = tmp_path / "queue.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "final_review_queue.py",
            str(proposals),
            "--out",
            str(out),
            "--resolved-by",
            str(resolved),
        ],
    )
    queue.main()

    written = json.loads(out.read_text())
    assert written["summary"]["reconciled_items"] == 1
    assert written["summary"]["client_review_items"] == len(written["items"]) == 1
    assert written["summary"]["resolution_artifacts"] == ["classification_consensus.json"]
    assert "retained, not closed" in " ".join(written["summary"]["findings"])
    assert written["reconciled"][0]["document_id"] == "doc-001"
    assert "Retained as already resolved: 1" in capsys.readouterr().out


def test_an_answered_register_entry_and_inapplicable_arithmetic_are_not_questions(tmp_path):
    """An answer the run holds is not put to the client as a question.

    An attribution register entry carrying a Phase 0 reason code, and a document
    whose arithmetic is established as not applicable, were both queued -- and
    the canonical export then refused each document for the item it held.
    """
    from client_review import queue

    register = tmp_path / "unattributable.json"
    register.write_text(
        json.dumps(
            {
                "register": [
                    {
                        "document_id": "doc-001",
                        "reason_code": "payment_record",
                        "is_failure": False,
                    },
                    {"document_id": "doc-002", "reason_code": "unresolved", "is_failure": True},
                ]
            }
        )
    )
    arithmetic = tmp_path / "arithmetic.json"
    arithmetic.write_text(
        json.dumps(
            {
                "documents": [
                    {"document_id": "doc-003", "arithmetic_status": "not_applicable"},
                    {"document_id": "doc-004", "arithmetic_status": "not_provable"},
                    {"document_id": "doc-005", "arithmetic_status": "proved"},
                ]
            }
        )
    )
    items, _, _ = queue.consolidate([register, arithmetic])
    assert sorted((item["document_id"], item["reason"]) for item in items) == [
        ("doc-002", "register_review_required"),
        ("doc-004", "arithmetic_not_provable"),
    ]


def test_a_re_read_governs_the_findings_on_its_own_pages(tmp_path, capsys, monkeypatch):
    """A finding on a reading the record no longer holds is retained, not asked."""
    from client_review import queue

    base = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(1), consensus_item(2)]
    )
    reread = exceptions_file(
        tmp_path, "consensus_reread_exceptions.json", [consensus_item(1, "no_majority")]
    )
    manifest = tmp_path / "ingestion_manifest_reread.json"
    manifest.write_text(json.dumps({"pages": [{"page_id": "doc-001", "document_id": "doc-001"}]}))
    out = tmp_path / "queue.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "final_review_queue.py",
            str(base),
            str(reread),
            "--out",
            str(out),
            "--supersede",
            "consensus_exceptions.json",
            str(manifest),
        ],
    )
    queue.main()

    written = json.loads(out.read_text())
    assert sorted((item["document_id"], item["reason"]) for item in written["items"]) == [
        ("doc-001", "no_majority"),
        ("doc-002", "partial_disagreement"),
    ]
    assert (written["summary"]["superseded_items"], written["summary"]["reconciled_items"]) == (
        1,
        0,
    )
    superseded = written["reconciled"][0]
    assert (superseded["document_id"], superseded["disposition"], superseded["governed_by"]) == (
        "doc-001",
        "superseded_by_re_read",
        "ingestion_manifest_reread.json",
    )
    printed = capsys.readouterr().out
    assert "Retained as superseded by a re-read: 1" in printed
    assert "already resolved" not in printed

    # Rule 9: a supersession naming no input, or a manifest naming no page,
    # supersedes nothing and must not read as though it had.
    with pytest.raises(ValueError, match="not an input"):
        queue.governed_pages([("absent.json", str(manifest))], [str(base)])
    unnamed = tmp_path / "unnamed_manifest.json"
    unnamed.write_text(json.dumps({"pages": [{"page_label": "x"}]}))
    with pytest.raises(ValueError, match="names no page"):
        queue.governed_pages([("consensus_exceptions.json", str(unnamed))], [str(base)])


def test_a_vote_settles_the_findings_on_the_fields_it_accepted(tmp_path, capsys, monkeypatch):
    """A field the record already accepted by a vendor vote is not asked again."""
    from client_review import queue

    base = exceptions_file(
        tmp_path, "consensus_reread_exceptions.json", [consensus_item(1), consensus_item(2)]
    )
    # Another control's finding on the same field is a different question.
    other = exceptions_file(
        tmp_path, "validation_exceptions.json", [consensus_item(1, "implausible_identifier_format")]
    )
    vote = tmp_path / "vote_reread.json"
    vote.write_text(
        json.dumps(
            {
                "summary": {"artifact_type": "multi_engine_vote_v1"},
                "resolutions": [
                    {
                        "document_id": "doc-001",
                        "field": "lines[1].description",
                        "resolved_value": "Chair",
                        "resolved_by": "vendor_agreement",
                        "agreeing_vendors": 2,
                    }
                ],
            }
        )
    )
    out = tmp_path / "queue.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "final_review_queue.py",
            str(base),
            str(other),
            "--out",
            str(out),
            "--settled-by-vote",
            str(vote),
            "consensus_reread_exceptions.json",
        ],
    )
    queue.main()

    written = json.loads(out.read_text())
    assert sorted((item["document_id"], item["reason"]) for item in written["items"]) == [
        ("doc-001", "implausible_identifier_format"),
        ("doc-002", "partial_disagreement"),
    ]
    assert written["summary"]["settled_by_vote_items"] == 1
    assert written["summary"]["settled_by_vote"] == [
        [str(vote), "consensus_reread_exceptions.json"]
    ]
    settled = written["reconciled"][0]
    assert (settled["disposition"], settled["settled_by"], settled["resolved_value"]) == (
        "settled_by_vendor_vote",
        "vote_reread.json",
        "Chair",
    )
    assert "Retained as settled by a vendor vote: 1" in capsys.readouterr().out

    # Rule 9: a settlement naming no input, or a file that is not a vote,
    # settles nothing and must not read as though it had.
    with pytest.raises(ValueError, match="not an input"):
        queue.vote_settlements([(str(vote), "absent.json")], [str(base)])
    for shape in ({"summary": {"artifact_type": "something_else"}}, ["not", "a", "vote"]):
        wrong = tmp_path / "wrong.json"
        wrong.write_text(json.dumps(shape))
        with pytest.raises(ValueError, match="not a vote artifact"):
            queue.vote_settlements([(str(wrong), base.name)], [str(base)])


def test_a_pack_asks_nothing_the_final_queue_retains(tmp_path, capsys):
    """The pack lane reconciles the same three ways the final queue does.

    It builds the queue by its own path and took only ``--resolved-by``, so a
    pack from the final queue's inputs asked every finding the queue retains as
    superseded by a re-read or settled by a vendor vote: 522 and 532 on the
    commission run, where with both options it asks exactly the queue's items.
    """
    base = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(i) for i in (1, 2, 3)]
    )
    reread = exceptions_file(
        tmp_path,
        "consensus_reread_exceptions.json",
        [consensus_item(1, "no_majority"), consensus_item(4)],
    )
    manifest = tmp_path / "ingestion_manifest_reread.json"
    manifest.write_text(json.dumps({"pages": [{"page_id": "doc-001", "document_id": "doc-001"}]}))
    vote = tmp_path / "vote_reread.json"
    vote.write_text(
        json.dumps(
            {
                "summary": {"artifact_type": "multi_engine_vote_v1"},
                "resolutions": [
                    {
                        "document_id": "doc-004",
                        "field": "lines[4].description",
                        "resolved_value": "Chair",
                        "resolved_by": "vendor_agreement",
                        "agreeing_vendors": 2,
                    }
                ],
            }
        )
    )
    out = tmp_path / "pack"
    argv = [
        "build",
        str(base),
        str(reread),
        "--out-dir",
        str(out),
        "--force",
        "--supersede",
        base.name,
        str(manifest),
        "--settled-by-vote",
        str(vote),
        reread.name,
    ]
    assert cli.main(argv) == 0
    printed = capsys.readouterr().out
    assert "retained as superseded by a re-read: 1" in printed
    assert "retained as settled by a vendor vote: 1" in printed
    assert "already resolved" not in printed

    pack = json.loads((out / "review_pack.json").read_text())
    assert sorted((item["document_id"], item["reason"]) for item in pack["queue_items"]) == [
        ("doc-001", "no_majority"),
        ("doc-002", "partial_disagreement"),
        ("doc-003", "partial_disagreement"),
    ]
    retained = {entry["review_item_id"]: entry for entry in pack["reconciled"]}
    assert sorted((entry["document_id"], entry["disposition"]) for entry in retained.values()) == [
        ("doc-001", "superseded_by_re_read"),
        ("doc-004", "settled_by_vendor_vote"),
    ]
    # No group, and so no question, is built over a retained finding.
    grouped = {item_id for group in pack["groups"] for item_id in group["source_item_ids"]}
    assert grouped and not grouped & set(retained)
    summary = pack["summary"]
    assert (
        summary["reconciled_items"],
        summary["superseded_items"],
        summary["settled_by_vote_items"],
    ) == (0, 1, 1)
    assert summary["superseded_by"] == [[base.name, str(manifest)]]
    assert summary["settled_by_vote"] == [[str(vote), reread.name]]


def test_a_pack_refuses_what_the_final_queue_refuses(tmp_path, monkeypatch):
    """A reconciliation that matches no input is refused before a pack is written."""
    base = exceptions_file(tmp_path, "consensus_exceptions.json", [consensus_item(1)])
    manifest = tmp_path / "ingestion_manifest_reread.json"
    manifest.write_text(json.dumps({"pages": [{"page_id": "doc-001"}]}))
    not_a_vote = exceptions_file(tmp_path, "validation_exceptions.json", [])
    with pytest.raises(ValueError, match="not an input"):
        lane.build_pack([base], tmp_path / "a", supersede=[(Path("absent.json"), manifest)])
    with pytest.raises(ValueError, match="not an input"):
        lane.build_pack([base], tmp_path / "b", settled_by_vote=[(not_a_vote, Path("absent.json"))])
    with pytest.raises(ValueError, match="not a vote artifact"):
        lane.build_pack([base], tmp_path / "c", settled_by_vote=[(not_a_vote, Path(base.name))])
    # A pack whose every finding is retained has nothing to ask, and says so
    # rather than reporting a pack built from nothing.
    with pytest.raises(ValueError, match="all 1 findings .* retained as reconciled"):
        lane.build_pack([base], tmp_path / "d", supersede=[(Path(base.name), manifest)])
    # The command reports a refusal rather than writing a pack.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_lane.py",
            "build",
            str(base),
            "--out-dir",
            str(tmp_path / "e"),
            "--supersede",
            "absent.json",
            str(manifest),
        ],
    )
    with pytest.raises(SystemExit, match="Client review lane failed: .*not an input"):
        cli.main()
    assert not any((tmp_path / name).exists() for name in "abcde")


def dispositions_file(tmp_path: Path, entries: list, **overrides) -> Path:
    """Write an operator disposition artifact, overriding any top-level field."""
    path = tmp_path / "dispositions.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "operator_item_dispositions_v1",
                "authorization_status": "operator_authorized",
                "authorization_id": "run-authorization-amendment-63",
                "dispositions": entries,
                **overrides,
            }
        )
    )
    return path


def test_an_operator_disposition_retains_the_items_it_names(tmp_path, capsys, monkeypatch):
    """An authorized disposition is retained with its rule, never deleted -- in both paths."""
    from client_review import queue

    base = exceptions_file(
        tmp_path, "consensus_exceptions.json", [consensus_item(1), consensus_item(2)]
    )
    items, _, _ = queue.consolidate([base])
    target = next(item for item in items if item["document_id"] == "doc-001")
    disposed = dispositions_file(
        tmp_path,
        [
            {
                "review_item_id": target["review_item_id"],
                "rule": "a provider's own review flag is provenance",
                "evidence": "Amendment 63, rule 2",
            },
            {"review_item_id": "review-item-0000000000000000", "rule": "matches no item"},
        ],
    )
    out = tmp_path / "queue.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["final_review_queue.py", str(base), "--out", str(out), "--dispositions", str(disposed)],
    )
    queue.main()

    written = json.loads(out.read_text())
    assert [item["document_id"] for item in written["items"]] == ["doc-002"]
    retained = written["reconciled"][0]
    assert (
        retained["disposition"],
        retained["disposition_rule"],
        retained["disposition_authorization_id"],
        retained["disposed_by"],
        retained["disposition_evidence"],
    ) == (
        "dispositioned_by_operator",
        "a provider's own review flag is provenance",
        "run-authorization-amendment-63",
        "dispositions.json",
        "Amendment 63, rule 2",
    )
    summary = written["summary"]
    assert (summary["dispositioned_items"], summary["dispositions_matching_no_item"]) == (1, 1)
    assert summary["disposition_artifacts"] == ["dispositions.json"]
    printed = capsys.readouterr().out
    assert "Retained as dispositioned by an operator: 1" in printed
    assert "Dispositions matching no queue item: 1" in printed

    # The review pack retains the same item, as the queue does.
    argv = ["build", str(base), "--out-dir", str(tmp_path / "pack"), "--force"]
    assert cli.main([*argv, "--dispositions", str(disposed)]) == 0
    assert "retained as dispositioned by an operator: 1" in capsys.readouterr().out
    pack = json.loads((tmp_path / "pack" / "review_pack.json").read_text())
    assert [item["document_id"] for item in pack["queue_items"]] == ["doc-002"]
    assert pack["summary"]["disposition_artifacts"] == ["dispositions.json"]


def test_a_disposition_that_is_not_authorized_disposes_of_nothing(tmp_path):
    """No model output and no client note can dispose of a finding by itself."""
    from client_review import queue

    entry = {"review_item_id": "review-item-1", "rule": "a rule"}
    for overrides, message in (
        ({"artifact_type": "something_else"}, "not an operator disposition artifact"),
        ({"authorization_status": "proposed"}, "operator-authorized"),
        ({"authorization_id": ""}, "operator-authorized"),
        ({"dispositions": []}, "names no item"),
        ({"dispositions": [{"review_item_id": "review-item-1"}]}, "a review_item_id and a rule"),
        ({"dispositions": ["not an object"]}, "a review_item_id and a rule"),
    ):
        path = dispositions_file(tmp_path, [entry], **overrides)
        with pytest.raises(ValueError, match=message):
            queue.operator_dispositions([path])
    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[]")
    with pytest.raises(ValueError, match="not an operator disposition artifact"):
        queue.operator_dispositions([not_an_object])


def test_a_question_the_pack_has_no_wording_for_is_named_not_hidden():
    """A generically-phrased question still gets asked, and says that it is.

    `disagreement_family` falls back to the raw reason, so a finding family the
    template catalogue does not know becomes its own family with the default
    wording: the client sees a page and is told only to confirm it. Restoring
    the handwriting findings to the gate put 3,958 items behind that prompt
    before this was measured.
    """
    groups = [
        {
            "group_id": "decision_group:00001",
            "template_family": "commission_report",
            "finding_family": "single_reading",
            "item_count": 1,
            "document_count": 1,
            "source_item_indexes": [0],
        },
        {
            "group_id": "decision_group:00002",
            "template_family": "commission_report",
            "finding_family": "a_family_nobody_wrote_wording_for",
            "item_count": 1,
            "document_count": 1,
            "source_item_indexes": [1],
        },
    ]
    items = [
        {"document_id": "d1", "field": "commission_amount", "reason": "single_engine"},
        {"document_id": "d2", "field": "commission_amount", "reason": "something_new"},
    ]
    built = questions.build_questions(groups, items)
    summary = built["summary"]
    assert summary["generic_wording_question_count"] == 1
    assert summary["items_behind_generic_wording"] == 1
    assert summary["families_without_wording"] == ["a_family_nobody_wrote_wording_for"]
    known = next(q for q in built["questions"] if q["finding_family"] == "single_reading")
    assert known["generic_wording"] is False


def test_the_handwriting_families_are_asked_about_in_words_a_client_can_answer():
    """These reach the pack now, and 'confirm these items' is not a question.

    `missing_or_conflicting_semantic_type` asks what a mark is *for* -- a
    handwritten figure beside a printed one is either a correction or a note,
    and the two give opposite answers. `google_handwriting_region_unreadable`
    says plainly that we could not read it rather than offering a guess.
    """
    for family in (
        "missing_or_conflicting_semantic_type",
        "google_handwriting_region_unreadable",
        "insufficient_independent_agreement",
        "unresolved_source_template_mapping",
        "refinement_source_value_differs",
    ):
        template = questions.template_for(family)
        assert template is not questions.DEFAULT_TEMPLATE, family
        assert "handwriting" in template["headline"].lower() or "read" in (
            template["headline"].lower() + template["what_we_found"].lower()
        ), family
        # Every template offers the client a way out that is not a guess.
        assert template["answer_options"], family


def test_a_question_names_the_documents_and_fields_it_is_about():
    """The pack asked the client to confirm "these documents" and "the columns".

    It named neither. Everything needed was already on the group --
    `template_family` says what we think they are and `field_families` carries
    the captions the mapping lanes derived from the documents themselves -- and
    neither the workbook nor the instructions rendered any of it, so a question
    covering 360 documents and 4,393 findings arrived with one example id.
    """
    groups = [
        {
            "group_id": "decision_group:00001",
            "template_family": "commission_statement",
            "finding_family": "provider_or_schema_exception",
            "field_families": ["commission_amount", "mapping_70_of_commission"],
            "item_count": 2,
            "document_count": 2,
            "document_ids": ["p0001", "p0002"],
            "source_item_indexes": [0, 1],
        }
    ]
    items = [
        {"document_id": "p0001", "field": "commission_amount", "reason": "schema_exception"},
        {"document_id": "p0002", "field": "commission_amount", "reason": "schema_exception"},
    ]
    question = questions.build_questions(groups, items)["questions"][0]
    assert question["what_we_think_these_are"] == "commission_statement"
    # Rendered in the client's vocabulary, not the pipeline's.
    assert question["fields_in_question"] == "commission amount, 70 of commission"
    assert question["documents_in_question"] == ["p0001", "p0002"]
    assert question["documents_in_question_total"] == 2


def test_a_question_covering_many_documents_points_at_the_full_list():
    """A list of four hundred ids is not context; it is noise."""
    ids = [f"p{index:04d}" for index in range(400)]
    groups = [
        {
            "group_id": "decision_group:00001",
            "template_family": "commission_report",
            "finding_family": "single_reading",
            "field_families": [f"field_{index}" for index in range(40)],
            "item_count": 400,
            "document_count": 400,
            "document_ids": ids,
            "source_item_indexes": [0],
        }
    ]
    question = questions.build_questions(groups, [{"document_id": "p0000"}])["questions"][0]
    assert len(question["documents_in_question"]) == questions.NAMED_LIMIT
    assert question["documents_in_question_total"] == 400
    assert question["fields_in_question"].endswith(f"and {40 - questions.NAMED_LIMIT} more")


def test_a_question_with_nothing_to_name_says_nothing_rather_than_something_empty():
    assert questions.readable_fields([]) == ""
    assert questions.readable_fields(["", "  "]) == ""


def test_the_pdf_names_the_documents_and_points_at_the_rest():
    """A question spanning hundreds of documents names some and says where the rest are."""
    page = instructions.question_page(
        {
            "question_id": "Q001",
            "headline": "Can you confirm the layout?",
            "what_we_found": "The values did not fit.",
            "why_it_matters": "We do not guess.",
            "what_we_need": "Confirm what the columns mean.",
            "resolves_item_count": 4393,
            "document_count": 360,
            "what_we_think_these_are": "commission_statement",
            "fields_in_question": "commission amount, 70 of commission",
            "documents_in_question": ["p0001", "p0002"],
            "documents_in_question_total": 360,
        }
    )
    rendered = b"".join(page.parts).decode("latin-1")
    assert "commission statement" in rendered
    assert "commission amount, 70 of commission" in rendered
    assert "p0001, p0002" in rendered
    assert "and 358 more" in rendered

    # A question whose documents all fit names them and adds no pointer.
    short = instructions.question_page(
        {
            "question_id": "Q002",
            "headline": "Confirm these.",
            "what_we_found": "x",
            "why_it_matters": "y",
            "what_we_need": "z",
            "resolves_item_count": 1,
            "document_count": 1,
            "documents_in_question": ["p0001"],
            "documents_in_question_total": 1,
        }
    )
    assert "more. The full list" not in b"".join(short.parts).decode("latin-1")


def test_every_answer_a_client_can_give_reaches_the_authorization_step():
    """The pack and the compiler spoke different languages, and nothing checked.

    `client_decision_compile.py` accepts four fixed `decision_choice` values.
    This module offered clients thirty-eight answer options of its own, and the
    two sets shared not one string, so every returned answer compiled as
    non-actionable. A client could answer every question correctly and none of
    it could reach an authorized change.
    """
    import importlib

    compile_module = importlib.import_module("client_decision_compile")
    allowed = compile_module.ACTIONABLE | compile_module.NON_ACTIONABLE
    templates = list(questions.QUESTION_TEMPLATES.items()) + [
        ("<default>", questions.DEFAULT_TEMPLATE)
    ]
    for family, template in templates:
        choice = template.get("decision_choice")
        assert choice, f"{family} offers a client answers with no compiler choice"
        assert choice in allowed, f"{family}: {choice!r} is not a choice the compiler accepts"
        # A template that offers real options must map to an actionable choice,
        # or the client is asked a question whose answer can never be applied.
        if template.get("answer_options"):
            assert choice in compile_module.ACTIONABLE, family


def test_a_built_question_and_its_answer_carry_the_choice_through():
    groups = [
        {
            "group_id": "decision_group:00001",
            "template_family": "commission_statement",
            "finding_family": "document_type_proposal",
            "item_count": 1,
            "document_count": 1,
            "source_item_indexes": [0],
        }
    ]
    built = questions.build_questions(groups, [{"document_id": "d1"}])
    assert built["questions"][0]["decision_choice"] == "Confirm document family/type"


def test_no_question_is_mapped_to_a_choice_that_cannot_be_applied():
    """A choice whose change types have no consumer is a dead end with a signature on it.

    `client_decision_compile.py` says it plainly: only `template_registry_rule`
    has an implemented consumer, and a plan built from anything else compiles,
    binds a real operator signature, and *then* fails at apply. "Confirm
    document type" permits only `document_type_rule`, so a question mapped to it
    sends the client's answer down exactly that path. "Confirm document
    family/type" permits `template_registry_rule` and reaches
    `classification_amend.py`.
    """
    import importlib

    compile_module = importlib.import_module("client_decision_compile")
    appliable = "template_registry_rule"
    templates = list(questions.QUESTION_TEMPLATES.values()) + [questions.DEFAULT_TEMPLATE]
    for template in templates:
        choice = template["decision_choice"]
        if choice not in compile_module.ACTIONABLE:
            continue
        assert appliable in compile_module.CHOICE_CHANGE_TYPES[choice], (
            f"{choice!r} permits {sorted(compile_module.CHOICE_CHANGE_TYPES[choice])}, "
            f"none of which has an implemented consumer"
        )


def test_examples_are_spread_across_the_group_not_taken_off_the_top():
    """One example off the top cost a client a wrong answer on 3,043 findings.

    A question covering 285 documents showed the group's first page. That page
    was blank, and the client answered "Blank Page. Can be ignored." for all of
    them. The other 284 carried a median of 91 populated fields and up to 701.
    Applying that answer would have discarded 3,043 findings across 278 dense
    commission reports.
    """
    items = [{"document_id": f"p{index:04d}", "page_id": f"p{index:04d}"} for index in range(285)]
    chosen = [example["document_id"] for example in questions.examples_for(items)]
    assert len(chosen) == questions.EXAMPLE_LIMIT
    # First, middle and last -- so a group that is not uniform shows it.
    assert chosen == ["p0000", "p0142", "p0284"]
    assert len(set(chosen)) == len(chosen)


def test_a_small_group_shows_every_document_it_has():
    items = [{"document_id": "a", "page_id": "a"}, {"document_id": "b", "page_id": "b"}]
    assert [e["document_id"] for e in questions.examples_for(items)] == ["a", "b"]
    assert [e["document_id"] for e in questions.examples_for([])] == [""]
    with pytest.raises(ValueError):
        questions.examples_for(items, limit=0)


def test_one_document_appearing_many_times_is_still_one_example():
    """Members are items, not documents; the same page must not fill every slot."""
    items = [{"document_id": "a", "page_id": "a", "field": f"f{n}"} for n in range(9)]
    items += [{"document_id": "b", "page_id": "b"}]
    assert [e["document_id"] for e in questions.examples_for(items)] == ["a", "b"]


def test_every_example_gets_its_own_sheet_and_is_numbered():
    import importlib

    evidence_module = importlib.import_module("client_review.evidence")
    question = {
        "question_id": "Q005",
        "example": {"document_id": "a", "page_id": "a"},
        "examples": [
            {"document_id": "a", "page_id": "a"},
            {"document_id": "b", "page_id": "b"},
        ],
    }
    # The singular example is not rendered twice.
    assert [e["page_id"] for e in evidence_module.each_example(question)] == ["a", "b"]


def test_a_malformed_example_entry_is_skipped_not_rendered():
    """An `examples` list is data like any other and may not be all objects."""
    import importlib

    evidence_module = importlib.import_module("client_review.evidence")
    question = {
        "question_id": "Q001",
        "example": {"document_id": "a", "page_id": "a"},
        "examples": ["not an object", None, {"document_id": "b", "page_id": "b"}],
    }
    assert [e["page_id"] for e in evidence_module.each_example(question)] == ["a", "b"]
    # A question with no example at all yields nothing rather than raising.
    assert evidence_module.each_example({"question_id": "Q002"}) == []


def test_a_prior_answer_follows_its_group_not_its_question_number():
    """Question numbers are positional and do not survive a rebuild.

    Correcting how findings are grouped renumbers everything after it, so a
    prior round's Q005 is not this round's Q005. The pair the grouping itself
    indexes on -- template family and finding family -- does survive, and an
    answer follows the question it was actually about.
    """
    from client_review import answers as answers_module

    previous = {
        "answers": [
            {
                "question_id": "Q005",
                "template_family": "commission_report",
                "finding_family": "provider_or_schema_exception",
                "answer": "",
                "note": "Blank Page. Can be ignored.",
            },
            {
                "question_id": "Q021",
                "template_family": "commission_statement",
                "finding_family": "provider_extraction_failure",
                "answer": "A better scan exists",
                "note": "",
            },
            # An untouched question carries nothing forward.
            {
                "question_id": "Q099",
                "template_family": "commission_report",
                "finding_family": "single_reading",
                "answer": "",
                "note": "   ",
            },
        ]
    }
    rebuilt = [
        {
            "question_id": "Q012",
            "template_family": "commission_report",
            "finding_family": "provider_or_schema_exception",
        },
        {
            "question_id": "Q013",
            "template_family": "commission_report",
            "finding_family": "single_reading",
        },
    ]
    carried = answers_module.carry_forward(previous, rebuilt)
    assert carried["questions_prefilled"] == 1
    assert carried["carried"]["Q012"]["note"] == "Blank Page. Can be ignored."
    assert carried["carried"]["Q012"]["carried_from_question_id"] == "Q005"
    assert "Q013" not in carried["carried"]
    # An answer to a question that no longer exists is reported, not dropped
    # silently -- on the commission run those were answers to a question the
    # pack should never have asked.
    assert carried["previous_answers_with_no_matching_question"] == [
        {"template_family": "commission_statement", "finding_family": "provider_extraction_failure"}
    ]


def test_carried_answers_are_written_into_the_cells_the_client_types_in(tmp_path):
    from client_review import workbook as workbook_module

    payload = {
        "summary": {"question_count": 1, "queue_item_count": 1, "items_covered": 1},
        "questions": [
            {
                "question_id": "Q001",
                "headline": "Confirm the layout?",
                "how_your_answer_is_used": "informs a recheck",
                "resolves_item_count": 1,
                "document_count": 1,
                "priority": "normal",
                "client_answer_permitted": True,
                "answer_options": ["Yes", "No"],
                "example": {"document_id": "d1", "field": "f"},
            }
        ],
    }
    out = tmp_path / "wb.xlsx"
    workbook_module.write_workbook(
        payload, [], out, {"Q001": {"answer": "Yes", "note": "as discussed"}}
    )
    import zipfile

    text = zipfile.ZipFile(out).read("xl/sharedStrings.xml").decode("utf-8", "ignore")
    assert "as discussed" in text


def test_a_carried_answer_left_standing_is_still_an_answer(tmp_path):
    """Prefill and the "only what changed" rule were each right and jointly wrong.

    The importer counts an answer as what the client *changed*, so a "No answer
    needed" placeholder never becomes a decision. `--prefill-from` then wrote
    real carried answers into those same cells, and the rule discarded them: on
    the second commission round 21 of 25 answers were reported unanswered,
    leaving only the two questions that had nothing pre-filled.

    A cell matching what was carried is an answer the client reviewed and let
    stand. A cell matching a placeholder is still nothing.
    """
    from client_review import answers as answers_module
    from client_review import workbook as workbook_module

    question = {
        "question_id": "Q001",
        "headline": "Confirm the layout?",
        "how_your_answer_is_used": "informs a recheck",
        "resolves_item_count": 5,
        "document_count": 1,
        "priority": "normal",
        "client_answer_permitted": True,
        "answer_options": ["Yes", "No"],
        "example": {"document_id": "d1", "field": "f"},
    }
    placeholder = {**question, "question_id": "Q002", "client_answer_permitted": False}
    carried = {"Q001": {"answer": "Yes", "note": "as before"}}
    pack = {
        "summary": {"question_count": 2, "queue_item_count": 5, "items_covered": 5},
        "questions": [question, placeholder],
        "carried_answers": {"carried": carried},
    }
    issued = tmp_path / "issued.xlsx"
    workbook_module.write_workbook(pack, [], issued, carried)
    returned = tmp_path / "returned.xlsx"
    returned.write_bytes(issued.read_bytes())

    result = answers_module.read_answers(returned, issued, pack)
    assert result["summary"]["answered"] == 1
    assert result["summary"]["answers_reaffirmed_from_a_previous_round"] == 1
    answer = result["answers"][0]
    assert answer["question_id"] == "Q001"
    assert (answer["answer"], answer["note"]) == ("Yes", "as before")
    assert answer["reaffirmed_carried_answer"] is True
    # The placeholder row is untouched and stays unanswered.
    assert result["unanswered_questions"] == ["Q002"]


def test_a_blank_page_is_never_the_worked_example(tmp_path):
    """A client shown a blank page and asked about a layout answers about the blank page.

    That is exactly what happened. A question covering 285 documents led with
    the group's first page, which was blank; the client answered "Blank Page.
    Can be ignored." for all 285 and reaffirmed it twice. Only 4 of the 285 were
    blank and 194 carried more than fifty populated fields.

    `scan_profile.py` had flagged that page `near_blank` all along. Nothing read
    it.
    """
    members = [
        {"document_id": "p0019", "page_id": "p0019"},
        {"document_id": "p0382", "page_id": "p0382"},
        {"document_id": "p0687", "page_id": "p0687"},
    ]
    chosen = [e["document_id"] for e in questions.examples_for(members)]
    assert "p0019" in chosen  # without the flag it is picked, and led

    informed = questions.examples_for(members, uninformative=frozenset({"p0019"}))
    assert [e["document_id"] for e in informed] == ["p0382", "p0687"]
    # The singular example follows the same rule, because it is what leads.
    built = questions.build_questions(
        [
            {
                "group_id": "g1",
                "template_family": "commission_report",
                "finding_family": "single_reading",
                "item_count": 3,
                "document_count": 3,
                "source_item_indexes": [0, 1, 2],
            }
        ],
        members,
        uninformative=frozenset({"p0019"}),
    )
    assert built["questions"][0]["example"]["document_id"] == "p0382"


def test_a_group_that_is_genuinely_all_blank_still_shows_a_page():
    """Excluding every candidate would leave a question with no evidence at all."""
    members = [{"document_id": "p0019", "page_id": "p0019"}]
    chosen = questions.examples_for(members, uninformative=frozenset({"p0019"}))
    assert [e["document_id"] for e in chosen] == ["p0019"]


def test_near_blank_pages_are_read_from_the_profile_that_already_flags_them(tmp_path):
    from client_review import evidence as evidence_module

    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "pages": [
                    {"page_index": 0, "quality_problems": ["near_blank", "dpi_below_300"]},
                    {"page_index": 1, "quality_problems": ["dpi_below_300"]},
                ]
            }
        )
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "pages": [
                    {"page_id": "p0001", "source_page_number": 1},
                    {"page_id": "p0002", "source_page_number": 2},
                ]
            }
        )
    )
    assert evidence_module.uninformative_pages(profile, manifest) == frozenset({"p0001"})
    # A missing profile makes a worse pack, never a wrong one.
    assert evidence_module.uninformative_pages(None, manifest) == frozenset()
    assert evidence_module.uninformative_pages(profile, None) == frozenset()
    assert evidence_module.uninformative_pages(tmp_path / "absent.json", manifest) == frozenset()


def test_a_profile_flagging_nothing_and_a_manifest_that_is_ragged(tmp_path):
    """A manifest is data like any other: entries may be malformed or unnamed."""
    from client_review import evidence as evidence_module

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "pages": [
                    "not an object",
                    {"source_page_number": 1},  # flagged, but names no page
                    {"page_id": "p0002", "source_page_number": 2},
                    {"page_id": "p0003", "source_page_number": None},
                ]
            }
        )
    )
    flags = tmp_path / "flagged.json"
    flags.write_text(json.dumps({"pages": [{"page_index": 0, "quality_problems": ["near_blank"]}]}))
    # The flagged page names no identifier, so nothing is excluded.
    assert evidence_module.uninformative_pages(flags, manifest) == frozenset()

    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"pages": [{"page_index": 0, "quality_problems": []}]}))
    assert evidence_module.uninformative_pages(clean, manifest) == frozenset()


def _lane_pack_and_answers():
    pack = {
        "groups": [
            {
                "group_id": "decision_group:00001",
                "template_family": "commission_report",
                "finding_family": "document_type_proposal",
                "item_count": 3,
                "document_ids": ["d1", "d2"],
                "source_item_ids": ["i1", "i2", "i3"],
                "protected": False,
            },
            {
                "group_id": "decision_group:00002",
                "template_family": "commission_report",
                "finding_family": "arithmetic_or_reassembly",
                "item_count": 5,
                "document_ids": ["d3"],
                "source_item_ids": ["i4"],
                "protected": True,
            },
        ]
    }
    answers = {
        "answers": [
            {
                "question_id": "Q001",
                "group_id": "decision_group:00001",
                "decision_choice": "Confirm document family/type",
                "answer": "",
                "note": "These are commission reports.",
            }
        ]
    }
    return pack, answers


def test_a_lane_answer_reaches_the_compiler(tmp_path):
    """There were two ways into client review and only one reached authorization.

    `client_decision_compile.py` takes a safe-consolidation artifact and a
    workbook-import proposal keyed to its hash. The review lane produced
    neither, so a client could answer every question correctly and none of it
    could be compiled, authorized or applied -- 23 answers speaking to 27,855
    review items, with no route.
    """
    import importlib

    compile_module = importlib.import_module("client_decision_compile")
    from client_review import compile_inputs

    pack, answers = _lane_pack_and_answers()
    issued = tmp_path / "issued.xlsx"
    issued.write_bytes(b"issued")
    returned = tmp_path / "returned.xlsx"
    returned.write_bytes(b"returned")

    consolidation = compile_inputs.consolidation_from_pack(pack)
    imported = compile_inputs.decisions_from_answers(pack, answers, consolidation, issued, returned)

    # The compiler's own validators, unmodified, must accept both.
    decisions = compile_module.validate_decisions(imported)
    groups = compile_module.decision_groups(consolidation)
    assert len(decisions) == len(groups) == 2
    assert imported["safe_consolidation_sha256"] == compile_module.digest(consolidation)
    assert all(d["decision_id"] in groups for d in decisions)


def test_every_group_carries_a_decision_even_when_unanswered(tmp_path):
    """The compiler requires `issued_group_count` to equal the decision count.

    A group the client did not answer is recorded as an explicit empty decision
    rather than omitted, so silence is silence and not a shorter list.
    """
    from client_review import compile_inputs

    pack, answers = _lane_pack_and_answers()
    issued = tmp_path / "i.xlsx"
    issued.write_bytes(b"i")
    returned = tmp_path / "r.xlsx"
    returned.write_bytes(b"r")
    consolidation = compile_inputs.consolidation_from_pack(pack)
    imported = compile_inputs.decisions_from_answers(pack, answers, consolidation, issued, returned)
    assert imported["issued_group_count"] == imported["decision_count"] == 2
    assert imported["answered_group_count"] == 1
    unanswered = next(d for d in imported["decisions"] if d["decision_id"].endswith("00002"))
    assert unanswered["client_decision"] == ""
    assert unanswered["client_comment"] == ""


def test_protection_is_copied_from_the_group_never_asserted(tmp_path):
    """The compiler refuses a decision whose protection differs from its group's."""
    from client_review import compile_inputs

    pack, answers = _lane_pack_and_answers()
    issued = tmp_path / "i.xlsx"
    issued.write_bytes(b"i")
    returned = tmp_path / "r.xlsx"
    returned.write_bytes(b"r")
    consolidation = compile_inputs.consolidation_from_pack(pack)
    imported = compile_inputs.decisions_from_answers(pack, answers, consolidation, issued, returned)
    by_id = {d["decision_id"]: d for d in imported["decisions"]}
    for group in pack["groups"]:
        assert by_id[group["group_id"]]["protected"] is group["protected"]


def test_a_dropdown_selection_reaches_the_compiler_as_the_alternative(tmp_path):
    """The compiler reads the alternative from `client_comment`.

    A client who picked an option and wrote no note has still answered, and the
    selection must not be dropped on the way.
    """
    from client_review import compile_inputs

    pack, _ = _lane_pack_and_answers()
    answers = {
        "answers": [
            {
                "question_id": "Q001",
                "group_id": "decision_group:00001",
                "decision_choice": "Provide alternative in comment",
                "answer": "The line items govern",
                "note": "",
            }
        ]
    }
    issued = tmp_path / "i.xlsx"
    issued.write_bytes(b"i")
    returned = tmp_path / "r.xlsx"
    returned.write_bytes(b"r")
    consolidation = compile_inputs.consolidation_from_pack(pack)
    imported = compile_inputs.decisions_from_answers(pack, answers, consolidation, issued, returned)
    answered = next(d for d in imported["decisions"] if d["decision_id"].endswith("00001"))
    assert answered["client_comment"] == "The line items govern"


def test_a_pack_with_no_groups_is_refused(tmp_path):
    from client_review import compile_inputs

    with pytest.raises(ValueError, match="no groups"):
        compile_inputs.consolidation_from_pack({"groups": []})


def test_compile_inputs_refuses_to_overwrite(tmp_path):
    from client_review.lane import write_compile_inputs

    pack, answers = _lane_pack_and_answers()
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(pack))
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps(answers))
    issued = tmp_path / "i.xlsx"
    issued.write_bytes(b"i")
    returned = tmp_path / "r.xlsx"
    returned.write_bytes(b"r")
    consolidation_out = tmp_path / "c.json"
    decisions_out = tmp_path / "d.json"
    write_compile_inputs(
        pack_path, answers_path, issued, returned, consolidation_out, decisions_out
    )
    assert consolidation_out.is_file() and decisions_out.is_file()
    with pytest.raises(FileExistsError):
        write_compile_inputs(
            pack_path, answers_path, issued, returned, consolidation_out, decisions_out
        )


def test_an_answer_naming_no_group_reaches_no_decision(tmp_path):
    """An answer must find its group by id; anything else is ignored, not guessed."""
    from client_review import compile_inputs

    pack, _ = _lane_pack_and_answers()
    answers = {
        "answers": [
            {"question_id": "Q001", "group_id": None, "decision_choice": "x", "note": "n"},
            {"question_id": "Q002", "group_id": "", "decision_choice": "x", "note": "n"},
            {"question_id": "Q003", "decision_choice": "x", "note": "n"},
        ]
    }
    issued = tmp_path / "i.xlsx"
    issued.write_bytes(b"i")
    returned = tmp_path / "r.xlsx"
    returned.write_bytes(b"r")
    consolidation = compile_inputs.consolidation_from_pack(pack)
    imported = compile_inputs.decisions_from_answers(pack, answers, consolidation, issued, returned)
    assert imported["answered_group_count"] == 0
    assert all(d["client_decision"] == "" for d in imported["decisions"])


def test_the_compile_inputs_command_writes_both_artifacts(tmp_path, monkeypatch, capsys):
    import importlib

    entry = importlib.import_module("client_review_lane")
    pack, answers = _lane_pack_and_answers()
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(pack))
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps(answers))
    for name in ("i.xlsx", "r.xlsx"):
        (tmp_path / name).write_bytes(name.encode())
    consolidation_out = tmp_path / "c.json"
    decisions_out = tmp_path / "d.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_lane.py",
            "compile-inputs",
            "--pack",
            str(pack_path),
            "--answers",
            str(answers_path),
            "--issued-workbook",
            str(tmp_path / "i.xlsx"),
            "--returned-workbook",
            str(tmp_path / "r.xlsx"),
            "--consolidation-out",
            str(consolidation_out),
            "--decisions-out",
            str(decisions_out),
        ],
    )
    assert entry.main() == 0
    out = capsys.readouterr().out
    assert "1 answered of 2 decision groups" in out
    assert "nothing is authorized" in out
    assert json.loads(consolidation_out.read_text())["decision_groups"]
    assert json.loads(decisions_out.read_text())["decision_count"] == 2


def test_the_compile_inputs_command_reports_a_refusal(tmp_path, monkeypatch):
    import importlib

    entry = importlib.import_module("client_review_lane")
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps({"groups": []}))
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps({"answers": []}))
    for name in ("i.xlsx", "r.xlsx"):
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_lane.py",
            "compile-inputs",
            "--pack",
            str(pack_path),
            "--answers",
            str(answers_path),
            "--issued-workbook",
            str(tmp_path / "i.xlsx"),
            "--returned-workbook",
            str(tmp_path / "r.xlsx"),
            "--consolidation-out",
            str(tmp_path / "c.json"),
            "--decisions-out",
            str(tmp_path / "d.json"),
        ],
    )
    with pytest.raises(SystemExit) as raised:
        entry.main()
    assert "no groups" in str(raised.value)


def test_compile_inputs_stays_silent_when_asked_to(tmp_path, monkeypatch, capsys):
    import importlib

    entry = importlib.import_module("client_review_lane")
    pack, answers = _lane_pack_and_answers()
    pack_path = tmp_path / "pack.json"
    pack_path.write_text(json.dumps(pack))
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps(answers))
    for name in ("i.xlsx", "r.xlsx"):
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_review_lane.py",
            "compile-inputs",
            "--pack",
            str(pack_path),
            "--answers",
            str(answers_path),
            "--issued-workbook",
            str(tmp_path / "i.xlsx"),
            "--returned-workbook",
            str(tmp_path / "r.xlsx"),
            "--consolidation-out",
            str(tmp_path / "c.json"),
            "--decisions-out",
            str(tmp_path / "d.json"),
            "--quiet",
        ],
    )
    assert entry.main() == 0
    assert capsys.readouterr().out == ""
    # Silence suppresses the summary only; the artifacts are unchanged.
    assert (tmp_path / "c.json").is_file() and (tmp_path / "d.json").is_file()


def test_one_protected_answer_does_not_block_every_other_decision(tmp_path):
    """`client_decision_compile.py` refuses the whole run, not just the one group.

    Rule 7 is right: a protected finding informs an item-by-item recheck and is
    never cleared in a batch. So the compiler refuses an actionable choice on a
    protected group -- and it raises, which ends the entire compilation.

    Carrying the client's dropdown choice straight through therefore meant that
    one answered protected group blocked every unprotected decision in the same
    run. On the commission round that was 20 answered protected groups blocking
    the 3 unprotected document-type answers the client had also given, so the
    lane's answers still could not reach the authorization step.

    The answer is recorded as deferred and kept in full: what the client chose is
    retained beside it, and the comment is untouched.
    """
    from client_review import compile_inputs

    issued = tmp_path / "issued.xlsx"
    issued.write_bytes(b"issued")
    returned = tmp_path / "returned.xlsx"
    returned.write_bytes(b"returned")

    pack, answers = _lane_pack_and_answers()
    consolidation = compile_inputs.consolidation_from_pack(pack)
    consolidation["decision_groups"][1]["protected"] = True
    for group in consolidation["decision_groups"]:
        group.setdefault("document_ids", ["d1"])
    answers["answers"] = [
        {
            "question_id": "Q001",
            "group_id": "decision_group:00001",
            "decision_choice": "Confirm document family/type",
            "note": "Payment Record",
        },
        {
            "question_id": "Q002",
            "group_id": "decision_group:00002",
            "decision_choice": "Provide alternative in comment",
            "note": "use the sum of line items",
        },
    ]
    result = compile_inputs.decisions_from_answers(pack, answers, consolidation, issued, returned)
    by_id = {entry["decision_id"]: entry for entry in result["decisions"]}

    unprotected = by_id["decision_group:00001"]
    assert unprotected["client_decision"] == "Confirm document family/type"
    assert unprotected["deferred_because_protected"] is False
    assert unprotected["client_decision_as_answered"] is None

    protected = by_id["decision_group:00002"]
    assert protected["client_decision"] == "Defer"
    assert protected["deferred_because_protected"] is True
    # Nothing the client said is lost.
    assert protected["client_decision_as_answered"] == "Provide alternative in comment"
    assert protected["client_comment"] == "use the sum of line items"


def test_an_unanswered_protected_group_is_not_marked_deferred_by_this_rule(tmp_path):
    """No answer is already the empty choice; it was not deferred, it was never made."""
    from client_review import compile_inputs

    issued = tmp_path / "issued.xlsx"
    issued.write_bytes(b"issued")
    returned = tmp_path / "returned.xlsx"
    returned.write_bytes(b"returned")

    pack, answers = _lane_pack_and_answers()
    consolidation = compile_inputs.consolidation_from_pack(pack)
    consolidation["decision_groups"][1]["protected"] = True
    answers["answers"] = []
    result = compile_inputs.decisions_from_answers(pack, answers, consolidation, issued, returned)
    for entry in result["decisions"]:
        assert entry["client_decision"] == ""
        assert entry["deferred_because_protected"] is False
        assert entry["client_decision_as_answered"] is None


def test_readings_are_concatenated_for_the_cell_beside_the_answer():
    """Answering a value question means choosing between readings, so show them.

    The values were carried on every example already but reached only the
    instruction PDF, so the workbook asked which reading was right while showing
    neither on the row being answered.
    """
    assert (
        questions.readings_summary(
            [{"values_read": ["974.16", "769.68"]}, {"values_read": ["974.16"]}]
        )
        == "974.16 | 769.68"
    )
    # A single reading is the confirm case: one engine read it, nobody disagreed.
    assert questions.readings_summary([{"values_read": ["308.39"]}]) == "308.39"
    # A question about something other than a reading has nothing to show, and
    # an empty cell says that more honestly than an invented placeholder.
    assert questions.readings_summary([{"values_read": []}, {}]) == ""
    many = [{"values_read": [str(n) for n in range(9)]}]
    summary = questions.readings_summary(many)
    assert summary.startswith("0 | 1 | 2 | 3 | 4 | 5") and summary.endswith("(+3 more)")


def test_the_workbook_shows_the_readings_immediately_before_the_answer():
    """Placement is the point: any further away and the values are off screen."""
    headers = [name for name, _, _ in workbook.QUESTION_COLUMNS]
    assert headers.index("What we read") == headers.index("Your answer") - 1
    assert headers.index("Your notes") == headers.index("Your answer") + 1


def test_a_written_workbook_carries_the_readings_into_the_answer_row(tmp_path):
    payload = {
        "pack_title": "Client review",
        "summary": {"question_count": 1},
        "questions": [
            {
                "question_id": "Q001",
                "headline": "Two systems read this commission amount differently.",
                "how_your_answer_is_used": "Your answer informs an item-by-item recheck.",
                "answer_options": ["The first value is correct", "The second value is correct"],
                "resolves_item_count": 2,
                "document_count": 1,
                "priority": "high",
                "client_answer_permitted": True,
                "what_we_read": "974.16 | 769.68",
                "example": {
                    "document_id": "northgate__p0227",
                    "field": "lines[3].commission_amount",
                },
            }
        ],
    }
    output = workbook.write_workbook(payload, [consensus_item(0)], tmp_path / "review.xlsx")
    cells = _read_sheet_cells(output, "xl/worksheets/sheet2.xml")
    assert cells["C3"] == "What we read"
    assert cells["C4"] == "974.16 | 769.68"
    # The answer box sits directly to its right, still empty for the client.
    assert cells["D3"] == "Your answer"


def test_the_pdf_prints_the_readings_so_it_agrees_with_the_workbook_row():
    """Both surfaces must show the values, or they disagree about the question.

    The client reads the PDF page and answers in the workbook row. If only one
    of them carries the readings, the other asks which value is right while
    showing none.
    """
    page = instructions.question_page(
        {
            "question_id": "Q001",
            "headline": "Two systems read this commission amount differently.",
            "what_we_found": "The readings disagree.",
            "why_it_matters": "The amount is paid on.",
            "what_we_need": "Tell us which is correct.",
            "resolves_item_count": 2,
            "document_count": 1,
            "what_we_read": "974.16 | 769.68",
        }
    )
    rendered = b"".join(page.parts).decode("latin-1")
    # Section labels are drawn uppercase.
    assert "WHAT WE READ" in rendered
    assert "974.16" in rendered and "769.68" in rendered

    # Nothing to show prints no heading, rather than an empty labelled block.
    quiet = instructions.question_page(
        {
            "question_id": "Q002",
            "headline": "Confirm the document type.",
            "what_we_found": "x",
            "why_it_matters": "y",
            "what_we_need": "z",
            "resolves_item_count": 1,
            "document_count": 1,
            "what_we_read": "",
        }
    )
    assert "WHAT WE READ" not in b"".join(quiet.parts).decode("latin-1")


def test_a_reason_sentence_does_not_become_its_own_group():
    """One cause had split into five families because the sentence carried data.

    `audit:` reasons were bounded after they produced 4,455 groups of one, but
    every other unrecognized reason still arrived as its own key. On this corpus
    that had already started: "independent model vendors disagree on the document
    type: openai payment_confirmation, x-ai remittance_advice" is one cause, and
    it reached grouping as five families because the vendors and the types it
    names vary from page to page.
    """
    from client_review import grouping

    variants = [
        "Independent model vendors disagree on the document type: "
        "openai payment_confirmation, x-ai remittance_advice",
        "Independent model vendors disagree on the document type: "
        "openai commission_statement, x-ai commission_report",
        "Independent model vendors disagree on the document type: "
        "openai sales_quote, x-ai remittance_advice",
    ]
    families = {grouping.disagreement_family(reason) for reason in variants}
    assert families == {"unclassified_finding"}

    # A recognized reason keeps its own family, and so does a short unrecognized
    # token: the bound removes sentences, not every reason without an entry.
    assert grouping.disagreement_family("no_majority") == "provider_disagreement"
    assert grouping.disagreement_family("slot_pair_classified") == "slot_pair_classified"
    # Measured over the commission corpus, every genuine family runs to eight
    # words or fewer, so a nine-word key is still allowed through as itself.
    nine = "no_engine_proposed_a_document_type_for_this_page"
    assert grouping.disagreement_family(nine) == nine
    assert grouping.disagreement_family(nine + "_and_one_more") == "unclassified_finding"

    # The audit route is unchanged: it buckets before this bound is reached.
    assert grouping.disagreement_family("audit:Something nobody wrote a phrase for") == (
        "audit_unclassified"
    )


def test_the_unclassified_bucket_is_asked_in_its_own_words():
    """Bounding the key must not push its items into the generic question.

    3,958 handwriting items once sat behind "We need your confirmation on these
    items" because their family had no wording here. The bucket introduced for
    unrecognized reasons would land in the same place without an entry.
    """
    from client_review import questions

    assert "unclassified_finding" in questions.QUESTION_TEMPLATES
    template = questions.QUESTION_TEMPLATES["unclassified_finding"]
    # The findings underneath are mixed, so the honest answer set includes
    # saying so rather than forcing one explanation for all of them.
    assert any("several different things" in option for option in template["answer_options"])


def test_the_handwriting_families_are_asked_in_their_own_words():
    """4,330 items reached a client as "We need your confirmation on these items".

    Both families come from the handwriting lane and neither is answerable as the
    generic prompt poses it: one is our own duplicate book-keeping, the other has
    no reading to confirm because the mark could not be read.
    """
    from client_review import questions

    for family in ("duplicate_engine_region", "missing_or_invalid_reading"):
        assert family in questions.QUESTION_TEMPLATES
        template = questions.QUESTION_TEMPLATES[family]
        assert template["answer_options"], family
        assert template["decision_choice"] in {
            "Approve proposed routing",
            "Provide alternative in comment",
            "Confirm document family/type",
            "Confirm document type",
        }
    # A duplicate outline is ours to collapse, so the client is told so plainly.
    assert "Nothing" in questions.QUESTION_TEMPLATES["duplicate_engine_region"]["what_we_need"]
    # An unread mark is not a blank field, and the wording has to keep them apart.
    unread = questions.QUESTION_TEMPLATES["missing_or_invalid_reading"]
    assert "not the same as an absent one" in unread["why_it_matters"]

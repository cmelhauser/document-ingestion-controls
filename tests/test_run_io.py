"""The shared reader of retained extractor responses.

Every lane that reads the independent extractor's pages reads them through
`run_io`. When each indexed the first file per page, the 222 pages of the
commission run read only in a retry were empty to all of them.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_io  # noqa: E402


def respond(path, text):
    """Write a retained extractor response carrying `text`."""
    path.write_text(json.dumps({"response": {"document": {"text": text}}}), encoding="utf-8")
    return path


def test_a_page_read_only_in_its_retry_is_indexed_to_the_retry(tmp_path):
    respond(tmp_path / "000001_run__p0001.json", "")
    respond(tmp_path / "000002_run__p0001__retry1.json", "HALVOR")
    respond(tmp_path / "000003_run__p0002.json", "Murbrook")
    respond(tmp_path / "000004_run__p0002__retry1.json", "Murbrook again")
    respond(tmp_path / "000005_run__p0003.json", "")
    respond(tmp_path / "000006_run__p0003__retry1.json", "")
    (tmp_path / "notes.txt").write_text("not a response")
    index = run_io.retained_responses(tmp_path)
    assert index["run__p0001"].name == "000002_run__p0001__retry1.json"
    # The first response that read the page is kept; a later one is not preferred.
    assert index["run__p0002"].name == "000003_run__p0002.json"
    # Neither read it: the first is kept, so the page is still accounted for.
    assert index["run__p0003"].name == "000005_run__p0003.json"
    assert set(index) == {"run__p0001", "run__p0002", "run__p0003"}


def test_a_response_text_is_read_tolerantly(tmp_path):
    assert run_io.response_document_text({"response": {"document": {"text": "x"}}}) == "x"
    assert run_io.response_document_text({"response": {"document": {"text": 7}}}) == ""
    assert run_io.response_document_text({"response": None}) == ""
    assert run_io.response_document_text([]) == ""
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert run_io.retained_response_text(bad) == ""
    assert run_io.retained_response_text(tmp_path / "absent.json") == ""


def token(text, start, end, top, bottom):
    """One positioned word as Document AI returns it."""
    corners = [{"x": 0.1, "y": top}, {"x": 0.2, "y": top}, {"x": 0.2, "y": bottom}]
    anchor = {"textSegments": [{"startIndex": str(start), "endIndex": str(end)}]}
    return {"layout": {"textAnchor": anchor, "boundingPoly": {"normalizedVertices": corners}}}


def test_positioned_words_carry_their_row_and_never_share_one_across_pages(tmp_path):
    """A row join reads these; a word on page two must sit on no row of page one."""
    text = "BU0597 391.95 SO1610"
    first = {
        "tokens": [
            token(text, 0, 6, 0.1, 0.12),
            token(text, 7, 13, 0.1, 0.12),
            # A word at the very start carries no startIndex, and a corner may
            # omit a coordinate that is zero.
            {
                "layout": {
                    "textAnchor": {"textSegments": [{"endIndex": "6"}]},
                    "boundingPoly": {"normalizedVertices": [{"x": 0.1}, {"y": 0.3}]},
                }
            },
            # No position, or no text: not a word on the page.
            {"layout": {"textAnchor": {"textSegments": [{"startIndex": "0", "endIndex": "6"}]}}},
            {"layout": {"textAnchor": {}, "boundingPoly": {"normalizedVertices": [{"y": 0.5}]}}},
        ]
    }
    second = {"tokens": [token(text, 14, 20, 0.1, 0.12)]}
    payload = {"response": {"document": {"text": text, "pages": [first, "not a page", second]}}}
    found = run_io.response_tokens(payload)
    assert found == [
        ("BU0597", 0.1, 0.12),
        ("391.95", 0.1, 0.12),
        ("BU0597", 0.0, 0.3),
        # The third page's word sits two pages down, on no row of the first.
        ("SO1610", 2.1, 2.12),
    ]
    assert run_io.response_tokens({"response": {"document": "nope"}}) == []
    assert run_io.response_tokens([]) == []
    assert (
        run_io.response_tokens({"response": {"document": {"text": None, "pages": [first]}}}) == []
    )
    path = tmp_path / "r.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert run_io.retained_response_tokens(path) == found
    assert run_io.retained_response_tokens(tmp_path / "absent.json") == []

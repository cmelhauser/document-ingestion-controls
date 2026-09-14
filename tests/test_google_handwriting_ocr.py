"""Tests for the Google Cloud Vision handwriting OCR lane; no live call occurs."""

import importlib
import json
import sys
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

lane = importlib.import_module("google_handwriting_ocr")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def manifest(tmp_path):
    write(tmp_path / "one.pdf", "%PDF-one")
    write(tmp_path / "two.pdf", "%PDF-two")
    return write(
        tmp_path / "manifest.json",
        {
            "pages": [
                {"page_id": "page/1", "page_pdf": "one.pdf", "source_page_number": 1},
                {"page_id": "page-2", "page_pdf": "two.pdf", "source_page_number": 2},
            ]
        },
    )


def regions(tmp_path):
    return write(
        tmp_path / "regions.json",
        [
            {
                "page_id": "page/1",
                "handwriting_regions": [
                    {
                        "region_id": "ink-1",
                        "box": {"left": 0.0, "top": 0.0, "right": 0.7, "bottom": 0.6},
                        "semantic_type": "damage_note",
                        "content_class": "text",
                    },
                    {
                        "region_id": "ink-empty",
                        "box": {"left": 0.8, "top": 0.8, "right": 1.0, "bottom": 1.0},
                    },
                ],
            }
        ],
    )


def vision_response(text="Paid 38"):
    words = []
    x = 10
    for value in text.split():
        words.append(
            {
                "boundingBox": {
                    "vertices": [
                        {"x": x, "y": 10},
                        {"x": x + 30, "y": 10},
                        {"x": x + 30, "y": 30},
                        {"x": x, "y": 30},
                    ]
                },
                "confidence": 0.9,
                "symbols": [{"text": character} for character in value],
            }
        )
        x += 40
    return {
        "responses": [
            {
                "fullTextAnnotation": {
                    "text": text,
                    "pages": [
                        {
                            "width": 100,
                            "height": 100,
                            "blocks": [{"paragraphs": [{"words": words}]}],
                        }
                    ],
                }
            }
        ]
    }


class HTTPResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.value).encode()


def fake_renderer(page_path, image_path, dpi):
    assert Path(page_path).suffix == ".pdf" and dpi == 300
    Path(image_path).write_bytes(b"png")


def test_endpoint_request_and_word_helpers(tmp_path):
    assert (
        lane.endpoint("project-1", "global") == "https://vision.googleapis.com/v1/images:annotate"
    )
    assert "/projects/project-1/locations/us/" in lane.endpoint("project-1", "us")
    with pytest.raises(ValueError, match="location"):
        lane.endpoint("project", "moon")
    with pytest.raises(ValueError, match="project"):
        lane.endpoint("bad/project", "us")
    image = tmp_path / "page.png"
    image.write_bytes(b"png")
    body = lane.request_body(image, ["en-t-i0-handwrit"])
    request = body["requests"][0]
    assert request["features"] == [{"type": "DOCUMENT_TEXT_DETECTION"}]
    assert request["imageContext"]["languageHints"] == ["en-t-i0-handwrit"]
    assert "imageContext" not in lane.request_body(image, [])["requests"][0]
    words = lane.word_evidence(vision_response()["responses"][0])
    assert [item["text"] for item in words] == ["Paid", "38"]
    assert words[0]["box"]["left"] == 0.1
    assert lane.word_evidence({"fullTextAnnotation": {"pages": ["skip"]}}) == []
    noisy = {
        "fullTextAnnotation": {
            "pages": [
                {
                    "width": 100,
                    "height": 100,
                    "blocks": [
                        "skip",
                        {
                            "paragraphs": [
                                "skip",
                                {
                                    "words": [
                                        "skip",
                                        {"symbols": [], "boundingBox": {"vertices": []}},
                                    ]
                                },
                            ]
                        },
                    ],
                }
            ]
        }
    }
    assert lane.word_evidence(noisy) == []
    assert lane.word_text({"symbols": ["skip", {"text": "A"}, {"text": 1}]}) == "A"
    assert lane.normalized_box({"vertices": []}, 0, 1) is None


def test_region_loading_binding_and_numeric_typing(tmp_path):
    assert lane.valid_box(None) is False
    assert lane.valid_box({}) is False
    loaded = lane.load_regions([regions(tmp_path)])
    assert [item["region_id"] for item in loaded["page/1"]] == ["ink-1", "ink-empty"]
    flat = write(
        tmp_path / "flat-regions.json",
        [
            {
                "page_id": "flat-page",
                "handwriting_regions": [
                    {
                        "region_id": "flat-1",
                        "left": 0.1,
                        "top": 0.1,
                        "right": 0.2,
                        "bottom": 0.2,
                    }
                ],
            }
        ],
    )
    assert lane.load_regions([flat])["flat-page"][0]["box"]["left"] == 0.1
    with pytest.raises(ValueError, match="duplicate region_id"):
        lane.load_regions([regions(tmp_path), regions(tmp_path)])
    bad = write(
        tmp_path / "bad-regions.json",
        [{"page_id": "p", "handwriting_regions": [{"region_id": "r", "box": {}}]}],
    )
    with pytest.raises(ValueError, match="normalized box"):
        lane.load_regions([bad])
    assert list(lane.region_records({"records": []})) == []
    assert list(lane.region_records({"page_id": "p", "handwriting_regions": []})) == []
    with pytest.raises(ValueError, match="object or list"):
        list(lane.region_records("bad"))
    with pytest.raises(ValueError, match="records must be objects"):
        list(lane.region_records(["bad"]))
    with pytest.raises(ValueError, match="must be a list"):
        list(lane.region_records({"page_id": "p", "handwriting_regions": {}}))
    missing = write(
        tmp_path / "missing-region.json",
        [
            {
                "page_id": "p",
                "handwriting_regions": [{"box": {"left": 0, "top": 0, "right": 1, "bottom": 1}}],
            }
        ],
    )
    with pytest.raises(ValueError, match="page_id and region_id"):
        lane.load_regions([missing])
    annotations, exceptions = lane.bind_regions(
        "page/1", loaded["page/1"], lane.word_evidence(vision_response()["responses"][0])
    )
    assert annotations[0]["value"] == "Paid 38"
    assert annotations[0]["semantic_type"] == "damage_note"
    assert annotations[1]["value"] is None
    assert exceptions[0]["reason"] == "google_handwriting_region_unreadable"
    assert lane.infer_content_class("$1,234.50") == "numeric"
    assert lane.infer_content_class("received") == "text"


def test_provider_transport_and_failure_categories():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return HTTPResponse(vision_response())

    result = lane.provider_request("https://example.test", {}, "token", "project", 2, opener)
    assert result["responses"] and calls[0][0].get_header("X-goog-user-project") == "project"
    with pytest.raises(ValueError, match="access token"):
        lane.provider_request("url", {}, "", "project", 1, opener)
    with pytest.raises(ValueError, match="HTTP 429"):
        lane.provider_request(
            "https://example.test",
            {},
            "token",
            "project",
            1,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPError("url", 429, "quota", {}, BytesIO())
            ),
        )
    with pytest.raises(ValueError, match="transport"):
        lane.provider_request(
            "https://example.test",
            {},
            "token",
            "project",
            1,
            lambda *a, **k: (_ for _ in ()).throw(URLError("x")),
        )
    assert lane.failure_type(ValueError("Cloud Vision HTTP 429")) == "cloud_vision_http_429"
    assert (
        lane.failure_type(ValueError("Cloud Vision transport failed"))
        == "cloud_vision_transport_failed"
    )
    invalid_json = type(
        "BadResponse",
        (),
        {"__enter__": lambda self: self, "__exit__": lambda *a: False, "read": lambda self: b"{"},
    )
    with pytest.raises(json.JSONDecodeError):
        lane.provider_request(
            "https://example.test", {}, "token", "project", 1, lambda *a, **k: invalid_json()
        )
    try:
        json.loads("{")
    except json.JSONDecodeError as exc:
        assert lane.failure_type(exc) == "cloud_vision_invalid_json_response"
    assert lane.failure_type(RuntimeError("x")) == "RuntimeError"


def test_run_retains_every_page_and_htr_contract(tmp_path):
    intake = manifest(tmp_path)
    region_path = regions(tmp_path)
    calls = []

    def opener(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise URLError("offline")
        return HTTPResponse(vision_response())

    result = lane.run_lane(
        intake,
        tmp_path / "htr.json",
        tmp_path / "adapter.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        tmp_path / "images",
        "project",
        "us",
        "token",
        region_paths=[region_path],
        max_retries=0,
        renderer=fake_renderer,
        opener=opener,
    )
    assert result == {"pages": 2, "annotations": 2, "review_items": 2}
    htr = json.loads((tmp_path / "htr.json").read_text())
    adapter = json.loads((tmp_path / "adapter.json").read_text())
    exceptions = json.loads((tmp_path / "exceptions.json").read_text())
    assert htr["engine"].startswith("google_cloud_vision_handwriting/")
    assert htr["independence_group"] == "google"
    assert len(adapter["records"]) == 2
    assert adapter["records"][0]["page_status"] == "ocr_complete_regions_bound"
    assert adapter["records"][1]["page_status"] == "open_exception"
    assert adapter["records"][1]["failure_type"] == "cloud_vision_transport_failed"
    assert {item["reason"] for item in exceptions["exceptions"]} == {
        "google_handwriting_region_unreadable",
        "cloud_vision_transport_failed",
    }
    with pytest.raises(ValueError, match="must be new"):
        lane.run_lane(
            intake,
            tmp_path / "other.json",
            tmp_path / "other-adapter.json",
            tmp_path / "other-exceptions.json",
            tmp_path / "raw",
            tmp_path / "other-images",
            "project",
            "us",
            "token",
        )


def test_limits_render_and_cli(monkeypatch, tmp_path, capsys):
    with pytest.raises(ValueError, match="positive"):
        lane.validate_limits(0, 1, 1, 1, 0, 300)
    with pytest.raises(ValueError, match="DPI"):
        lane.validate_limits(1, 1, 1, 1, 0, 10)
    with pytest.raises(ValueError, match="at most 300"):
        lane.validate_limits(1, 1, 1, 301, 0, 300)
    with pytest.raises(ValueError, match="non-negative"):
        lane.validate_limits(1, 1, 1, 1, -1, 300)
    monkeypatch.setattr(lane.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1})())
    with pytest.raises(ValueError, match="render"):
        lane.render_page("p.pdf", tmp_path / "x.png", 300)
    monkeypatch.setattr(lane.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"png")
    lane.render_page("p.pdf", existing, 300)
    monkeypatch.setattr(lane, "load_project_env", lambda: None)
    monkeypatch.setattr(lane, "run_lane", lambda *a, **k: {"pages": 1})
    monkeypatch.setenv("GOOGLE_HANDWRITING_OCR_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "google_handwriting_ocr.py",
            "manifest.json",
            "--out",
            "htr.json",
            "--adapter-out",
            "adapter.json",
            "--exceptions",
            "exceptions.json",
            "--raw-dir",
            "raw",
            "--images-dir",
            "images",
            "--project-id",
            "project",
            "--enable",
        ],
    )
    lane.main()
    assert json.loads(capsys.readouterr().out) == {"pages": 1}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "google_handwriting_ocr.py",
            "m",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
            "--images-dir",
            "i",
        ],
    )
    with pytest.raises(SystemExit, match="disabled"):
        lane.main()


def test_preflight_response_and_cli_failure_paths(monkeypatch, tmp_path):
    intake = manifest(tmp_path)
    kwargs = {
        "manifest_path": intake,
        "out_path": tmp_path / "o.json",
        "adapter_path": tmp_path / "a.json",
        "exceptions_path": tmp_path / "e.json",
        "raw_dir": tmp_path / "raw",
        "images_dir": tmp_path / "images",
        "project_id": "project",
        "location": "us",
        "access_token": "token",
        "renderer": fake_renderer,
        "opener": lambda *a, **k: HTTPResponse({"responses": []}),
        "max_pages": 1,
    }
    with pytest.raises(ValueError, match="exceed"):
        lane.run_lane(**kwargs)
    kwargs["max_pages"] = 2
    kwargs["adapter_path"] = kwargs["out_path"]
    with pytest.raises(ValueError, match="distinct"):
        lane.run_lane(**kwargs)
    kwargs["adapter_path"] = tmp_path / "a.json"
    kwargs["max_image_bytes"] = 1
    result = lane.run_lane(**kwargs)
    assert result["review_items"] == 2

    bad_response_root = tmp_path / "bad-responses"
    for name, response in (
        ("missing", {"responses": []}),
        ("error", {"responses": [{"error": {"code": 3}}]}),
    ):
        root = bad_response_root / name
        result = lane.run_lane(
            intake,
            root / "o.json",
            root / "a.json",
            root / "e.json",
            root / "raw",
            root / "images",
            "project",
            "us",
            "token",
            renderer=fake_renderer,
            opener=lambda *a, _response=response, **k: HTTPResponse(_response),
            max_retries=0,
        )
        assert result["review_items"] == 2

    original_bind = lane.bind_regions
    monkeypatch.setattr(
        lane, "bind_regions", lambda *a, **k: (_ for _ in ()).throw(ValueError("bind failed"))
    )
    bind_root = tmp_path / "bind-failure"
    result = lane.run_lane(
        intake,
        bind_root / "o.json",
        bind_root / "a.json",
        bind_root / "e.json",
        bind_root / "raw",
        bind_root / "images",
        "project",
        "us",
        "token",
        renderer=fake_renderer,
        opener=lambda *a, **k: HTTPResponse(vision_response()),
        max_retries=0,
    )
    assert result["review_items"] == 2
    assert "response" in json.loads(next((bind_root / "raw").iterdir()).read_text())
    monkeypatch.setattr(lane, "bind_regions", original_bind)

    monkeypatch.setattr(lane, "load_project_env", lambda: None)
    monkeypatch.setenv("GOOGLE_HANDWRITING_OCR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_HANDWRITING_OCR_CREDENTIAL_ENV", "bad-name")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "google_handwriting_ocr.py",
            "m",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
            "--images-dir",
            "i",
        ],
    )
    with pytest.raises(SystemExit, match="uppercase"):
        lane.main()
    monkeypatch.setenv(
        "GOOGLE_HANDWRITING_OCR_CREDENTIAL_ENV", "GOOGLE_HANDWRITING_OCR_ACCESS_TOKEN"
    )
    monkeypatch.setenv("GOOGLE_HANDWRITING_OCR_MAX_PAGES", "bad")
    with pytest.raises(SystemExit, match="must be an integer"):
        lane.main()
    monkeypatch.setenv("GOOGLE_HANDWRITING_OCR_MAX_PAGES", "1")
    # Supply the credential explicitly; otherwise the lane resolves it from ADC
    # before run_lane is reached and reports that instead.
    monkeypatch.setenv("GOOGLE_HANDWRITING_OCR_ACCESS_TOKEN", "ya29.test")
    monkeypatch.setattr(
        lane, "run_lane", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad run"))
    )
    with pytest.raises(SystemExit, match="bad run"):
        lane.main()


def test_two_detectors_proposing_the_same_page_is_the_point_not_a_collision(tmp_path):
    """`--regions` is repeatable, and the repeat is what makes a reading corroborated.

    A region_id is only meaningful inside the artifact that issued it, and every
    engine numbers its own regions from one. Requiring (page_id, region_id) to be
    unique across all supplied artifacts refused the whole run the first time two
    detectors both said `hw_1` -- which is to say, always. On a real run this
    failed with `duplicate region_id for page: hw_1` and the documented
    multi-detector usage could not execute at all.
    """
    primary = write(
        tmp_path / "primary.json",
        [
            {
                "page_id": "page/1",
                "handwriting_regions": [
                    {
                        "region_id": "hw_1",
                        "box": {"left": 0.0, "top": 0.0, "right": 0.4, "bottom": 0.4},
                    }
                ],
            }
        ],
    )
    secondary = write(
        tmp_path / "secondary.json",
        [
            {
                "page_id": "page/1",
                "handwriting_regions": [
                    # The same id from an independent detector, for a different box.
                    {
                        "region_id": "hw_1",
                        "box": {"left": 0.5, "top": 0.5, "right": 0.9, "bottom": 0.9},
                    },
                    {
                        "region_id": "hw_2",
                        "box": {"left": 0.1, "top": 0.6, "right": 0.3, "bottom": 0.8},
                    },
                ],
            }
        ],
    )
    loaded = lane.load_regions([primary, secondary])["page/1"]
    assert len(loaded) == 3

    # A contested id is qualified by detector so emitted ids stay unique per
    # page; an uncontested one is passed through untouched.
    by_id = {item["region_id"]: item for item in loaded}
    assert set(by_id) == {"primary:hw_1", "secondary:hw_1", "hw_2"}
    assert len({item["region_id"] for item in loaded}) == len(loaded)

    # The id each detector actually issued is retained, so a reading can still be
    # traced back to the region that engine proposed.
    assert by_id["primary:hw_1"]["detector_region_id"] == "hw_1"
    assert by_id["secondary:hw_1"]["detector_region_id"] == "hw_1"
    assert by_id["primary:hw_1"]["detector"] == "primary"
    assert by_id["hw_2"]["detector_region_id"] == "hw_2"
    # The boxes stay distinct: these are two proposals, not one merged region.
    assert by_id["primary:hw_1"]["box"] != by_id["secondary:hw_1"]["box"]

    # A single-detector run is unchanged -- nothing is qualified when nothing
    # is contested.
    assert [item["region_id"] for item in lane.load_regions([primary])["page/1"]] == ["hw_1"]

    # A genuine repeat inside one detector is still refused, and the message
    # names which artifact it was found in.
    twice = write(
        tmp_path / "twice.json",
        [
            {
                "page_id": "page/1",
                "handwriting_regions": [
                    {
                        "region_id": "hw_1",
                        "box": {"left": 0.0, "top": 0.0, "right": 0.4, "bottom": 0.4},
                    },
                    {
                        "region_id": "hw_1",
                        "box": {"left": 0.5, "top": 0.5, "right": 0.9, "bottom": 0.9},
                    },
                ],
            }
        ],
    )
    with pytest.raises(ValueError, match="duplicate region_id for page within twice"):
        lane.load_regions([twice])


class _RefreshingStub:
    """A token source that mints a new value each time it is refreshed."""

    def __init__(self):
        self.tokens = ["expired", "fresh"]
        self.refreshes = 0

    def __call__(self):
        return self.tokens[0]

    def refresh(self):
        self.refreshes += 1
        self.tokens.pop(0)
        return self.tokens[0]


def test_an_expired_vision_token_is_reminted_once():
    """Vision shares ADC with Document AI, and this lane has no resume path."""

    def opener(request, timeout):
        if request.get_header("Authorization") == "Bearer expired":
            raise HTTPError("url", 401, "expired", {}, BytesIO())
        return HTTPResponse(vision_response())

    source = _RefreshingStub()
    value = lane.request_with_refreshed_credential(
        "https://example.test", {}, source, "project", 2, opener
    )
    assert value["responses"]
    assert source.refreshes == 1


def test_a_second_vision_unauthorized_stays_an_explicit_failure():
    source = _RefreshingStub()
    with pytest.raises(lane.CloudVisionError, match="HTTP 401"):
        lane.request_with_refreshed_credential(
            "https://example.test",
            {},
            source,
            "project",
            1,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPError("url", 401, "no", {}, BytesIO())
            ),
        )
    assert source.refreshes == 1


def test_a_fixed_vision_token_string_is_never_refreshed():
    with pytest.raises(lane.CloudVisionError, match="HTTP 401"):
        lane.request_with_refreshed_credential(
            "https://example.test",
            {},
            "exported",
            "project",
            1,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPError("url", 401, "no", {}, BytesIO())
            ),
        )


def test_a_vision_quota_failure_is_not_retried_with_a_new_token():
    """429 is transient and belongs to bounded retry, not to credential refresh."""
    source = _RefreshingStub()
    with pytest.raises(lane.CloudVisionError, match="HTTP 429"):
        lane.request_with_refreshed_credential(
            "https://example.test",
            {},
            source,
            "project",
            1,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPError("url", 429, "quota", {}, BytesIO())
            ),
        )
    assert source.refreshes == 0

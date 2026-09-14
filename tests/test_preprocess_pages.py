"""Tests for immutable grayscale and binarized preprocessing variants."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

preprocess_pages = importlib.import_module("preprocess_pages")


def pgm(pixels=b"\x00\x80\xff"):
    return b"P5\n3 1\n255\n" + pixels


def invoke(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", [preprocess_pages.__file__, *map(str, args)])
    preprocess_pages.main()


def test_pgm_parse_and_threshold_errors(tmp_path):
    assert preprocess_pages.pgm_parts(pgm()) == (3, 1, b"\x00\x80\xff")
    assert preprocess_pages.pgm_parts(b"P5\n# note\n1 1\n255\n\x00") == (1, 1, b"\x00")
    for content, message in [
        (b"", "header"),
        (b"P2\n1 1\n255\n\x00", "Expected"),
        (b"P5\n2 1\n255\n\x00", "pixel count"),
    ]:
        with pytest.raises(ValueError, match=message):
            preprocess_pages.pgm_parts(content)
    source, output = tmp_path / "gray.pgm", tmp_path / "bin.pgm"
    source.write_bytes(pgm())
    preprocess_pages.threshold_pgm(source, output, 180)
    assert preprocess_pages.pgm_parts(output.read_bytes())[2] == b"\x00\x00\xff"
    enhanced, adaptive = tmp_path / "enhanced.pgm", tmp_path / "adaptive.pgm"
    preprocess_pages.enhance_pgm(source, enhanced)
    assert preprocess_pages.pgm_parts(enhanced.read_bytes())[2] == b"\x00\x80\xff"
    preprocess_pages.adaptive_threshold_pgm(enhanced, adaptive, radius=1, offset=0)
    assert len(preprocess_pages.pgm_parts(adaptive.read_bytes())[2]) == 3
    with pytest.raises(ValueError, match="Adaptive threshold"):
        preprocess_pages.adaptive_threshold_pgm(source, adaptive, radius=0)


def test_render_build_and_cli_paths(monkeypatch, tmp_path, capsys):
    module = preprocess_pages
    source, gray = tmp_path / "page.pdf", tmp_path / "gray.pgm"
    source.write_bytes(b"pdf")

    class Result:
        returncode = 0
        stderr = ""

    def succeeds(command, **_kwargs):
        Path(command[-1] + ".pgm").write_bytes(pgm())
        return Result()

    monkeypatch.setattr(module.subprocess, "run", succeeds)
    module.render_page(source, gray, 300)
    assert gray.is_file()

    class Failed:
        returncode = 1
        stderr = "failed"

    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: Failed())
    with pytest.raises(ValueError, match="pdftoppm failed"):
        module.render_page(source, tmp_path / "fail.pgm", 300)
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(ValueError, match="did not produce"):
        module.render_page(source, tmp_path / "none.pgm", 300)
    pages, out = tmp_path / "pages", tmp_path / "variants"
    pages.mkdir()
    (pages / "001.pdf").write_bytes(b"pdf")
    monkeypatch.setattr(
        module, "render_page", lambda _source, target, _dpi: target.write_bytes(pgm())
    )
    result = module.build_variants(pages, out, 150)
    assert len(result["pages"]) == 1 and Path(result["pages"][0]["grayscale_master"]).is_file()
    assert Path(result["pages"][0]["enhanced_grayscale"]).is_file()
    assert len(result["pages"][0]["binarized_variants"]) == 3
    with pytest.raises(ValueError, match="empty"):
        module.build_variants(pages, out, 150)
    with pytest.raises(ValueError, match="does not exist"):
        module.build_variants(tmp_path / "missing", tmp_path / "new", 150)
    output, manifest = tmp_path / "cli-out", tmp_path / "manifest.json"
    invoke(monkeypatch, pages, "--out", output, "--manifest", manifest, "--dpi", 150)
    assert json.loads(manifest.read_text())["dpi"] == 150
    assert "Preprocessed pages:" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="at least 72"):
        invoke(monkeypatch, pages, "--out", tmp_path / "bad", "--manifest", manifest, "--dpi", 1)


def test_a_directory_with_no_page_pdfs_is_refused(tmp_path):
    """A control that processed nothing has not passed.

    It previously wrote a manifest with an empty page list and a success message,
    leaving nothing downstream able to tell the variants did not exist.
    """
    empty = tmp_path / "pages"
    empty.mkdir()
    with pytest.raises(ValueError, match="No page PDFs found"):
        preprocess_pages.build_variants(empty, tmp_path / "out", 300)

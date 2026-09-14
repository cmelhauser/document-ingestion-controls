"""Regression checks for raster fallbacks embedded in release PDFs."""

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG_DIMENSIONS = re.compile(r'<svg[^>]*\bwidth="(\d+)"\s+height="(\d+)"')


def png_dimensions(path):
    """Read PNG dimensions without adding an image-library dependency."""
    signature = b"\x89PNG\r\n\x1a\n"
    payload = path.read_bytes()
    assert payload.startswith(signature)
    return struct.unpack(">II", payload[len(signature) + 8 : len(signature) + 16])


def test_lifecycle_png_fallbacks_match_their_svg_intrinsic_dimensions():
    """Prevent a square fallback from reserving blank vertical space in LaTeX."""
    for svg_path in sorted((ROOT / "assets").glob("*lifecycle.svg")):
        match = SVG_DIMENSIONS.search(svg_path.read_text())
        assert match, f"missing SVG width/height: {svg_path.name}"
        assert png_dimensions(svg_path.with_suffix(".svg.png")) == tuple(map(int, match.groups()))


def test_client_overview_lifecycle_canvas_tightly_frames_the_workflow():
    """Prevent a broad SVG viewBox from shrinking the workflow inside its PDF slot."""
    svg = (ROOT / "assets" / "client-overview-lifecycle.svg").read_text()
    assert 'viewBox="80 0 1640 369"' in svg

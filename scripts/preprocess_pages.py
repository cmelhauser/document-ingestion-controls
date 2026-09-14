#!/usr/bin/env python3
"""Create grayscale and threshold variants beside immutable one-page PDF masters."""

import argparse
import json
import subprocess
import sys
from array import array
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from runtime_config import env_int, load_project_env


def pgm_parts(data):
    """Split a binary P5 PGM into header metadata and pixels."""
    tokens, index = [], 0
    while len(tokens) < 4:
        while index < len(data) and data[index : index + 1].isspace():
            index += 1
        if index < len(data) and data[index : index + 1] == b"#":
            index = data.find(b"\n", index) + 1
            continue
        end = index
        while end < len(data) and not data[end : end + 1].isspace():
            end += 1
        if end == index:
            raise ValueError("Invalid PGM header")
        tokens.append(data[index:end])
        index = end
    if tokens[0] != b"P5" or tokens[3] != b"255":
        raise ValueError("Expected 8-bit binary PGM")
    while index < len(data) and data[index : index + 1].isspace():
        index += 1
    width, height = int(tokens[1]), int(tokens[2])
    pixels = data[index:]
    if len(pixels) != width * height:
        raise ValueError("PGM pixel count does not match dimensions")
    return width, height, pixels


def threshold_pgm(source, destination, threshold):
    """Write a binarized sibling PGM; leave the grayscale source untouched."""
    width, height, pixels = pgm_parts(Path(source).read_bytes())
    binary = bytes(0 if value < threshold else 255 for value in pixels)
    Path(destination).write_bytes(f"P5\n{width} {height}\n255\n".encode() + binary)


def enhance_pgm(source, destination):
    """Stretch usable contrast into an immutable enhancement sibling."""
    width, height, pixels = pgm_parts(Path(source).read_bytes())
    low, high = min(pixels), max(pixels)
    enhanced = (
        pixels
        if low == high
        else bytes(round((value - low) * 255 / (high - low)) for value in pixels)
    )
    Path(destination).write_bytes(f"P5\n{width} {height}\n255\n".encode() + enhanced)


def adaptive_threshold_pgm(source, destination, radius=15, offset=12):
    """Write a local-mean binarization sibling for uneven scans."""
    width, height, pixels = pgm_parts(Path(source).read_bytes())
    if radius < 1 or offset < 0:
        raise ValueError("Adaptive threshold radius must be positive and offset non-negative")
    stride = width + 1
    # A Python-int list costs hundreds of megabytes for a 300-DPI page and its
    # allocator retains that memory across the corpus loop.  A 64-bit packed
    # array preserves the exact sums while keeping one-page working memory
    # bounded, so a full run does not die after a handful of pages.
    integral = array("Q", [0]) * ((height + 1) * stride)
    for row in range(1, height + 1):
        running = 0
        for column in range(1, width + 1):
            running += pixels[(row - 1) * width + column - 1]
            integral[row * stride + column] = integral[(row - 1) * stride + column] + running
    binary = bytearray(width * height)
    for row in range(height):
        top, bottom = max(0, row - radius), min(height, row + radius + 1)
        for column in range(width):
            left, right = max(0, column - radius), min(width, column + radius + 1)
            total = (
                integral[bottom * stride + right]
                - integral[top * stride + right]
                - integral[bottom * stride + left]
                + integral[top * stride + left]
            )
            mean = total / ((bottom - top) * (right - left))
            binary[row * width + column] = (
                0 if pixels[row * width + column] < mean - offset else 255
            )
    Path(destination).write_bytes(f"P5\n{width} {height}\n255\n".encode() + bytes(binary))


def render_page(source, gray_path, dpi):
    """Use Poppler to render a source-page PDF without modifying that PDF."""
    command = [
        "pdftoppm",
        "-f",
        "1",
        "-l",
        "1",
        "-r",
        str(dpi),
        "-gray",
        "-singlefile",
        str(source),
        str(gray_path.with_suffix("")),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)  # noqa: S603 - fixed argv, no shell
    if result.returncode:
        raise ValueError(f"pdftoppm failed: {result.stderr.strip() or result.returncode}")
    if not gray_path.is_file():
        raise ValueError("pdftoppm did not produce a grayscale PGM")


def build_variants(pages_dir, out_dir, dpi):
    """Render immutable masters and complementary enhancement/binarization siblings."""
    source_dir, output_dir = Path(pages_dir), Path(out_dir)
    if not source_dir.is_dir():
        raise ValueError("Pages directory does not exist")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Variant output directory must be empty")
    sources = sorted(source_dir.glob("*.pdf"))
    if not sources:
        # A control that processed nothing has not passed. Writing a manifest with
        # an empty page list and a success message left nothing downstream able to
        # tell that the variants do not exist.
        raise ValueError(
            f"No page PDFs found in {source_dir}; intake writes them to a 'pages' "
            "subdirectory, so pass that directory rather than the run root"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    variants = []
    for source in sources:
        stem = source.stem
        gray = output_dir / f"{stem}__gray.pgm"
        global_variant = output_dir / f"{stem}__global.pgm"
        stroke_variant = output_dir / f"{stem}__stroke.pgm"
        render_page(source, gray, dpi)
        threshold_pgm(gray, global_variant, 180)
        threshold_pgm(gray, stroke_variant, 220)
        enhanced = output_dir / f"{stem}__enhanced.pgm"
        adaptive_variant = output_dir / f"{stem}__adaptive.pgm"
        enhance_pgm(gray, enhanced)
        adaptive_threshold_pgm(enhanced, adaptive_variant)
        variants.append(
            {
                "source_page_pdf": str(source),
                "grayscale_master": str(gray),
                "enhanced_grayscale": str(enhanced),
                "binarized_variants": [
                    str(global_variant),
                    str(stroke_variant),
                    str(adaptive_variant),
                ],
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "dpi": dpi,
        "pages": variants,
        "findings": [
            "Source page PDFs are immutable; grayscale, enhanced, and binarized variants are siblings.",
            "The ensemble is input for OCR/HTR comparison; it never replaces the grayscale master.",
        ],
    }


def main():
    load_project_env()
    parser = argparse.ArgumentParser(
        description="Create OCR-preprocessing variants without changing source pages."
    )
    parser.add_argument("pages", help="directory of immutable one-page PDFs from intake")
    parser.add_argument("--out", required=True, help="new or empty variant directory")
    parser.add_argument(
        "--dpi",
        type=int,
        default=env_int("PREPROCESS_DPI", 300),
        help="Resolution used to create each image variant.",
    )
    parser.add_argument(
        "--manifest", required=True, help="Destination path for the variant manifest."
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        if args.dpi < 72:
            raise ValueError("DPI must be at least 72")
        result = build_variants(args.pages, args.out, args.dpi)
    except (OSError, ValueError) as exc:
        sys.exit(f"Preprocessing failed: {exc}")
    Path(args.manifest).write_text(json.dumps(result, indent=2) + "\n")
    print(f"Preprocessed pages: {len(result['pages'])}")


if __name__ == "__main__":
    main()

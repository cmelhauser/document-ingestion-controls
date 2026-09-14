#!/usr/bin/env python3
"""
Phase 0.5 -- Scan corpus profiler.

Answers the colour-depth question empirically instead of asking the client to
recall five years of scanner settings. Produces the corpus colour map and the
per-page branch routing that drives the handwriting strategy.

Two traps this specifically looks for:

  false colour -- 24-bit RGB files containing only grey pixels, produced when a
                  scanner was set to colour but the source was a photocopy. Bit
                  depth alone misclassifies these as Branch A; the saturation
                  histogram is the operative test.

  JBIG2        -- lossy JBIG2 substitutes visually similar glyph patches across a
                  document and can alter digits with no visible artefact. Pages
                  compressed this way have all numerics marked suspect regardless
                  of OCR confidence.

Usage:
    python scan_profile.py /path/to/scans --out profile.json
    python scan_profile.py corpus.pdf --sample 200 --out profile.json
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime

import numpy as np
import pikepdf
from cli_help import apply_shared_help

HAVE_PIXEL_TOOLS = True


# Saturation above this counts as real chroma rather than scanner noise or JPEG
# colour fringing around printed glyph edges.
SATURATION_THRESHOLD = 0.12

# Fraction of pixels that must exceed the threshold before a page is called
# genuinely coloured. Kept low because pen annotation covers very little of a
# page -- a page can be 99% greyscale and still have a blue ink correction that
# matters more than everything printed on it.
CHROMA_PIXEL_FRACTION = 0.002

LOSSY_JBIG2_HINTS = {"/JBIG2Decode"}
BILEVEL_FILTERS = {"/CCITTFaxDecode", "/JBIG2Decode"}


def _filters(obj):
    """Normalize a PDF image /Filter entry to a list of strings."""
    f = obj.get("/Filter")
    if f is None:
        return []
    if isinstance(f, pikepdf.Array):
        return [str(x) for x in f]
    return [str(f)]


def _measure_quality(pil_img, max_dim=900):
    """
    Quality metrics for a scanned page. On a black-and-white corpus these drive
    enhancement routing and QA oversampling, because tone gives us nothing.

    Returns ink_coverage, contrast, sharpness, speckle_ratio.
    """
    if not HAVE_PIXEL_TOOLS:
        return {}
    img = pil_img.convert("L")
    img.thumbnail((max_dim, max_dim))
    a = np.asarray(img).astype(np.float32)
    if a.size == 0:
        return {}

    dark = a < 128
    ink = float(dark.mean())

    # Contrast measured as background-to-ink separation, not a percentile spread.
    # Document pages are mostly white: a p95-p5 spread reads ~0 on a sparse but
    # perfectly crisp page, which would condemn good scans as low contrast.
    bright = a[a >= 128]
    inkpx = a[dark]
    if bright.size and inkpx.size:
        background = float(np.median(bright))
        foreground = float(np.median(inkpx))
        contrast = max(0.0, (background - foreground) / 255.0)
    else:
        contrast = 0.0

    # Variance of the Laplacian: the standard blur proxy. Low values mean the
    # page is soft, which is where handwritten digits fail hardest.
    lap = a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:] - 4 * a[1:-1, 1:-1]
    sharpness = float(lap.var()) if lap.size else 0.0

    # Isolated dark pixels with no dark neighbour: scanner speckle rather than
    # text. High ratios mean despeckling is needed before OCR.
    if dark.shape[0] > 2 and dark.shape[1] > 2:
        core = dark[1:-1, 1:-1]
        neigh = dark[:-2, 1:-1].astype(np.int8) + dark[2:, 1:-1] + dark[1:-1, :-2] + dark[1:-1, 2:]
        isolated = np.logical_and(core, neigh == 0)
        speckle = float(isolated.sum() / max(core.sum(), 1))
    else:
        speckle = 0.0

    return {
        "ink_coverage": round(ink, 5),
        "contrast": round(contrast, 4),
        "sharpness": round(sharpness, 2),
        "speckle_ratio": round(speckle, 4),
    }


def quality_band(rec):
    """
    good / marginal / poor. Poor pages enter the QA sample regardless of stratum
    quotas -- they are where error concentrates and where a quota would
    under-represent them.
    """
    q = rec.get("quality") or {}
    dpi = rec.get("dpi") or 0
    problems = []

    if dpi and dpi < 200:
        problems.append("dpi_below_200")
    elif dpi and dpi < 300:
        problems.append("dpi_below_300")
    if q.get("contrast") is not None and q["contrast"] < 0.35:
        problems.append("low_contrast")
    if q.get("sharpness") is not None and q["sharpness"] < 120:
        problems.append("soft_focus")
    if q.get("speckle_ratio") is not None and q["speckle_ratio"] > 0.12:
        problems.append("heavy_speckle")
    if q.get("ink_coverage") is not None:
        if q["ink_coverage"] < 0.002:
            problems.append("near_blank")
        elif q["ink_coverage"] > 0.45:
            problems.append("over_inked_or_shadowed")

    severe = {"dpi_below_200", "low_contrast", "near_blank", "over_inked_or_shadowed"}
    if any(p in severe for p in problems) or len(problems) >= 3:
        band = "poor"
    elif problems:
        band = "marginal"
    else:
        band = "good"

    rec["quality_band"] = band
    rec["quality_problems"] = problems
    return rec


def _measure_chroma(pil_img, max_dim=600):
    """
    Return (chroma_fraction, mean_saturation) for an RGB image.

    Downsamples first: we are asking whether coloured ink exists anywhere on the
    page, which survives downsampling fine and is ~50x cheaper.
    """
    if not HAVE_PIXEL_TOOLS:
        return None, None
    img = pil_img.convert("RGB")
    img.thumbnail((max_dim, max_dim))
    a = np.asarray(img).astype(np.float32) / 255.0
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    with np.errstate(divide="ignore", invalid="ignore"):
        sat = np.where(mx > 0, (mx - mn) / mx, 0.0)
    # Ignore near-black pixels: printed text has unstable hue at low luminance
    # and would otherwise register as chroma.
    sat = np.where(mx > 0.15, sat, 0.0)
    frac = float((sat > SATURATION_THRESHOLD).mean())
    return frac, float(sat.mean())


def profile_page(page, page_index, deep=True):
    """Profile one PDF page. Returns a dict; never raises on a bad page."""
    rec = {
        "page_index": page_index,
        "bit_depth": None,
        "colorspace": None,
        "filters": [],
        "width_px": None,
        "height_px": None,
        "dpi": None,
        "chroma_fraction": None,
        "mean_saturation": None,
        "image_count": 0,
        "has_text_layer": False,
        "notes": [],
        "probe_failures": [],
    }

    try:
        rec["has_text_layer"] = bool(
            str(page.get("/Contents", "")) and "/Font" in str(page.get("/Resources", ""))
        )
    except Exception:
        # Defaults are indistinguishable from a successful measurement, so every
        # unread probe is named. profile_pdf already does this for a PDF that
        # cannot be opened; a per-page probe is held to the same standard.
        rec["probe_failures"].append("has_text_layer")

    try:
        media = page.get("/MediaBox")
        pw = float(media[2]) - float(media[0])
        ph = float(media[3]) - float(media[1])
    except Exception:
        pw = ph = None
        rec["probe_failures"].append("media_box")

    try:
        xobjects = page.Resources.XObject
    except (AttributeError, KeyError):
        xobjects = {}

    best = None  # largest image on the page drives the page verdict
    for _name, xobj in dict(xobjects).items():
        try:
            if str(xobj.get("/Subtype")) != "/Image":
                continue
        except Exception:
            # An unreadable XObject is not "no image"; the page verdict below is
            # drawn from whatever did parse, so the omission has to be visible.
            rec["probe_failures"].append("xobject_subtype")
            continue
        rec["image_count"] += 1
        try:
            w = int(xobj.get("/Width", 0))
            h = int(xobj.get("/Height", 0))
        except Exception:
            w = h = 0
        if best is None or w * h > best[1] * best[2]:
            best = (xobj, w, h)

    if best is None:
        rec["notes"].append("no_raster_image")
        return rec

    xobj, w, h = best
    rec["width_px"], rec["height_px"] = w, h
    rec["filters"] = _filters(xobj)

    try:
        rec["bit_depth"] = int(xobj.get("/BitsPerComponent", 0)) or None
    except Exception:
        rec["probe_failures"].append("bit_depth")
    try:
        cs = xobj.get("/ColorSpace")
        rec["colorspace"] = str(cs)[:80] if cs is not None else None
    except Exception:
        rec["probe_failures"].append("colorspace")

    if pw and ph and w and h:
        rec["dpi"] = round(min(w / pw * 72.0, h / ph * 72.0))

    if any(f in LOSSY_JBIG2_HINTS for f in rec["filters"]):
        rec["notes"].append("jbig2_compressed")

    rec["quality"] = {}
    if deep and HAVE_PIXEL_TOOLS:
        try:
            pi = pikepdf.PdfImage(xobj)
            pil = pi.as_pil_image()
            rec["quality"] = _measure_quality(pil)
            if rec["bit_depth"] and rec["bit_depth"] >= 8:
                frac, mean_sat = _measure_chroma(pil)
                rec["chroma_fraction"] = frac
                rec["mean_saturation"] = mean_sat
        except Exception as exc:
            rec["notes"].append(f"pixel_read_failed:{type(exc).__name__}")

    return rec


def classify(rec):
    """
    Assign a bucket and branch.

      C -> Branch A      true colour, measurable chroma
      G -> Branch B      grayscale, no chroma
      B -> Branch B      bilevel, degraded

    False colour (24-bit RGB with no chroma) is demoted from C to G. This is the
    check that bit depth alone gets wrong.
    """
    depth = rec.get("bit_depth")
    filters = rec.get("filters", [])
    chroma = rec.get("chroma_fraction")
    notes = rec["notes"]

    # Native-text pages have no source raster to assess. They are neither
    # grayscale nor a Branch B scan: route them to text extraction and document
    # classification, retaining the explicit marker for later provenance.
    if "no_raster_image" in notes:
        rec["bucket"] = "N"
        rec["branch"] = "native_text"
        rec["degraded"] = False
    elif any(f in BILEVEL_FILTERS for f in filters) or depth == 1:
        # B1 lossless vs B2 lossy matters: JBIG2 can substitute glyphs, so its
        # numerics are suspect in a way CCITT's are not.
        lossy = any(f in LOSSY_JBIG2_HINTS for f in filters)
        rec["bucket"] = "B2" if lossy else "B1"
        rec["branch"] = "B"
        rec["degraded"] = True
    elif depth and depth >= 8:
        cs = (rec.get("colorspace") or "").lower()
        looks_colour = ("rgb" in cs) or ("icc" in cs) or ("indexed" in cs)
        if chroma is None:
            # Could not read pixels. Trust the declared colourspace but flag it,
            # because an unverified Branch A assignment is exactly the mistake
            # false-colour detection exists to prevent.
            rec["bucket"] = "C" if looks_colour else "G"
            rec["branch"] = "A" if looks_colour else "B"
            notes.append("chroma_unverified")
        elif chroma >= CHROMA_PIXEL_FRACTION:
            rec["bucket"] = "C"
            rec["branch"] = "A"
        else:
            rec["bucket"] = "G"
            rec["branch"] = "B"
            if looks_colour:
                notes.append("false_colour")
        rec["degraded"] = False
    else:
        rec["bucket"] = "G"
        rec["branch"] = "B"
        rec["degraded"] = False
        if depth is None:
            # Same reasoning as chroma_unverified above: a verdict that rests on a
            # measurement we could not take must say so.
            notes.append("depth_unverified")

    rec["jbig2_suspect"] = "jbig2_compressed" in notes
    return quality_band(rec)


def profile_pdf(path, deep=True, limit=None):
    """Profile one PDF's pages for colour depth, resolution, and encoding hazards."""
    out = []
    try:
        pdf = pikepdf.open(path)
    except Exception as exc:
        return [
            {
                "page_index": None,
                "source_file": path,
                "notes": [f"open_failed:{type(exc).__name__}"],
                "bucket": None,
                "branch": None,
            }
        ]
    with pdf:
        producer = ""
        producer_probe_failed = False
        try:
            producer = str(pdf.docinfo.get("/Producer", ""))
        except Exception:
            producer_probe_failed = True
        for i, page in enumerate(pdf.pages):
            if limit and len(out) >= limit:
                break
            rec = classify(profile_page(page, i, deep=deep))
            rec["source_file"] = path
            rec["producer"] = producer[:120]
            if producer_probe_failed:
                rec["probe_failures"].append("document_producer")
            out.append(rec)
    return out


def collect_pdfs(target):
    """Collect every PDF under the supplied source root, in a stable order."""
    if os.path.isfile(target):
        return [target]
    found = []
    for root, _dirs, files in os.walk(target):
        for f in sorted(files):
            if f.lower().endswith(".pdf"):
                found.append(os.path.join(root, f))
    return found


def summarize(pages):
    """Aggregate per-page profiles into the corpus routing decision.

    Reports bucket, branch, and quality distributions plus the two traps that
    decide handwriting strategy and QA sizing: false colour (24-bit files with no
    measurable chroma) and JBIG2 pages, whose numerics stay suspect regardless of
    OCR confidence."""
    total = len(pages)
    buckets = Counter(p.get("bucket") for p in pages)
    branches = Counter(p.get("branch") for p in pages)
    bands = Counter(p.get("quality_band") for p in pages)
    problems = Counter(x for p in pages for x in (p.get("quality_problems") or []))
    dpis = [p["dpi"] for p in pages if p.get("dpi")]
    false_colour = sum(1 for p in pages if "false_colour" in p.get("notes", []))
    jbig2 = sum(1 for p in pages if p.get("jbig2_suspect"))
    unverified = sum(1 for p in pages if "chroma_unverified" in p.get("notes", []))
    failed = sum(
        1
        for p in pages
        if any(
            n.startswith("open_failed") or n.startswith("pixel_read_failed")
            for n in p.get("notes", [])
        )
    )

    def pct(n):
        return round(100.0 * n / total, 2) if total else 0.0

    n_c = buckets.get("C", 0)
    n_g = buckets.get("G", 0)
    n_b1 = buckets.get("B1", 0)
    n_b2 = buckets.get("B2", 0)
    n_native = buckets.get("N", 0)
    branch_b = branches.get("B", 0)
    bilevel = n_b1 + n_b2
    image_pages = total - n_native
    all_bw = image_pages > 0 and n_c == 0

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_pages_profiled": total,
        "image_pages_profiled": image_pages,
        "native_text_only_pages": n_native,
        "corpus_is_black_and_white": all_bw,
        "colour_map": {
            "C_true_colour": {"pages": n_c, "pct": pct(n_c)},
            "G_grayscale": {"pages": n_g, "pct": pct(n_g)},
            "B1_bilevel_lossless": {"pages": n_b1, "pct": pct(n_b1)},
            "B2_bilevel_lossy_jbig2": {"pages": n_b2, "pct": pct(n_b2)},
            "N_native_text_only": {"pages": n_native, "pct": pct(n_native)},
        },
        "branch_routing": {
            "A_ink_separation": {"pages": branches.get("A", 0), "pct": pct(branches.get("A", 0))},
            "B_morphology": {"pages": branch_b, "pct": pct(branch_b)},
            "native_text": {
                "pages": branches.get("native_text", 0),
                "pct": pct(branches.get("native_text", 0)),
            },
        },
        "quality_bands": {
            b: {"pages": bands.get(b, 0), "pct": pct(bands.get(b, 0))}
            for b in ("good", "marginal", "poor")
        },
        "quality_problems": dict(problems),
        "dpi": {
            "min": min(dpis) if dpis else None,
            "median": int(sorted(dpis)[len(dpis) // 2]) if dpis else None,
            "max": max(dpis) if dpis else None,
            "below_300": sum(1 for d in dpis if d < 300),
        },
        "hazards": {
            "false_colour_pages": false_colour,
            "jbig2_suspect_pages": jbig2,
            "chroma_unverified_pages": unverified,
            "read_failures": failed,
        },
        "findings": [],
    }

    f = summary["findings"]

    if n_native:
        f.append(
            f"{n_native} pages ({pct(n_native)}%) are native-text-only with no "
            "raster image to profile. Route these pages to text extraction and "
            "document classification; do not assign them a scan branch or treat "
            "them as scan-quality evidence."
        )
    if all_bw:
        f.append(
            "Corpus is entirely black-and-white. Ink separation is unavailable, so "
            "the compensating controls are the baseline architecture, not a "
            "fallback: run three OCR engines rather than two, treat arithmetic "
            "self-proof as the primary error-detection mechanism, assume "
            "handwriting is present until proven absent, and recover stamps by "
            "template matching rather than tone."
        )
    elif branch_b:
        f.append(
            f"{branch_b} pages ({pct(branch_b)}%) route to Branch B. Handwriting "
            "detection loses ink separation on these; detector output sets "
            "priority, never eligibility."
        )

    if n_g and bilevel:
        f.append(
            f"Mixed black-and-white corpus: {n_g} grayscale pages ({pct(n_g)}%) "
            f"vs {bilevel} bilevel ({pct(bilevel)}%). Grayscale retains the stroke "
            "intensity that morphology detection depends on; bilevel has discarded "
            "it irreversibly. Keep grayscale pages AS grayscale through the "
            "pipeline and derive binarized variants alongside — binarizing in "
            "place is a one-way loss."
        )
    elif n_g and not bilevel:
        f.append(
            f"All {n_g} pages are 8-bit grayscale. Better input than bilevel: the "
            "multi-binarization ensemble applies (global, adaptive local, and "
            "stroke-preserving thresholds, each an independent OCR voter), which "
            "recovers characters any single threshold loses at the cost of compute "
            "only."
        )
    elif bilevel and not n_g:
        f.append(
            f"All {bilevel} pages are 1-bit bilevel. Stroke intensity is already "
            "discarded, so the binarization ensemble is unavailable and morphology "
            "detection runs degraded. If grayscale or colour masters exist "
            "anywhere — original paper, an earlier scan generation, a scanner's "
            "retained output — re-deriving these pages is the cheapest accuracy "
            "improvement available in the project."
        )

    if n_b2:
        f.append(
            f"{n_b2} pages ({pct(n_b2)}%) use lossy JBIG2 compression, which "
            "substitutes visually similar glyph patches and can alter digits with "
            "no visible artefact. Mark all numerics from these suspect regardless "
            "of OCR confidence and escalate the stratum in QA sampling."
        )
    if false_colour:
        f.append(
            f"{false_colour} pages declare a colour colourspace but contain no "
            "measurable chroma (photocopied source scanned in colour mode). "
            "Demoted to Branch B — bit depth alone would have misrouted these."
        )
    if unverified:
        f.append(
            f"{unverified} pages could not be pixel-verified and were classified "
            "from the declared colourspace only. Treat Branch A assignments among "
            "them as provisional."
        )

    poor, marginal = bands.get("poor", 0), bands.get("marginal", 0)
    if poor:
        f.append(
            f"{poor} pages ({pct(poor)}%) score POOR on image quality "
            f"({', '.join(f'{k}: {v}' for k, v in problems.most_common(4))}). "
            "These enter the QA sample regardless of stratum quotas — a quota "
            "would under-represent exactly where error concentrates."
        )
    if marginal:
        f.append(
            f"{marginal} pages score MARGINAL and route to the enhancement pass "
            "(deskew, despeckle, contrast normalization) before OCR."
        )
    if summary["dpi"]["below_300"]:
        f.append(
            f"{summary['dpi']['below_300']} pages are below 300 DPI. Upscale these "
            "before any handwriting-region processing; sub-300 DPI is where "
            "handwritten digit recognition fails hardest."
        )
    if failed:
        f.append(
            f"{failed} pages could not be read and are excluded from these "
            "figures. They are not counted as clean."
        )
    if not f:
        f.append("No hazards detected.")

    if bilevel or poor:
        candidates = bilevel + poor
        summary["selective_rescan_estimate"] = {
            "priority_pages": candidates,
            "candidate_pages_low": int(candidates * 0.10),
            "candidate_pages_high": int(candidates * 0.20),
            "basis": (
                "Rescan only annotated pages, arithmetic-proof failures, and "
                "QA sample strata — typically 10-20% of the degraded "
                "population. Requires a better source (original paper or an "
                "earlier scan generation). Recovers most of the accuracy gap "
                "at a fraction of full-rescan cost, because annotations and "
                "errors concentrate in a minority of pages."
            ),
        }

    return summary


def main():
    ap = argparse.ArgumentParser(description="Phase 0.5 scan corpus profiler.")
    ap.add_argument("target", help="PDF file or directory of PDFs")
    ap.add_argument("--out", default="profile.json", help="output JSON path")
    ap.add_argument(
        "--sample",
        type=int,
        default=None,
        help="profile only the first N pages per file (calibration runs)",
    )
    ap.add_argument(
        "--shallow",
        action="store_true",
        help="skip pixel decoding; much faster, but cannot detect false colour",
    )
    ap.add_argument("--quiet", action="store_true")
    apply_shared_help(ap)
    args = ap.parse_args()

    pdfs = collect_pdfs(args.target)
    if not pdfs:
        sys.exit(f"No PDFs found at {args.target}")

    if not HAVE_PIXEL_TOOLS and not args.shallow:
        print(
            "WARNING: numpy/Pillow unavailable -- running shallow. "
            "False-colour detection is disabled.",
            file=sys.stderr,
        )

    all_pages = []
    for i, p in enumerate(pdfs, 1):
        if not args.quiet:
            print(f"[{i}/{len(pdfs)}] {os.path.basename(p)}", file=sys.stderr)
        all_pages.extend(profile_pdf(p, deep=not args.shallow, limit=args.sample))

    result = {"summary": summarize(all_pages), "pages": all_pages}
    with open(args.out, "w") as fh:
        json.dump(result, fh, indent=2)

    s = result["summary"]
    if not args.quiet:
        print(f"\nProfiled {s['total_pages_profiled']} pages across {len(pdfs)} file(s)")
        cm = s["colour_map"]
        print(f"  grayscale (G)        : {cm['G_grayscale']['pct']}%")
        print(f"  bilevel lossless (B1): {cm['B1_bilevel_lossless']['pct']}%")
        print(f"  bilevel JBIG2 (B2)   : {cm['B2_bilevel_lossy_jbig2']['pct']}%")
        print(f"  true colour (C)      : {cm['C_true_colour']['pct']}%")
        qb = s["quality_bands"]
        print(
            f"  quality: good {qb['good']['pct']}% / "
            f"marginal {qb['marginal']['pct']}% / poor {qb['poor']['pct']}%"
        )
        print("\nFindings:")
        for line in s["findings"]:
            print(f"  - {line}")
        print(f"\nWritten to {args.out}")


if __name__ == "__main__":
    main()

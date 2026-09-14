# Image, Handwriting, Reassembly, and Classification Controls

This reference governs the optional accuracy-improvement stages. All stages are
**evidence-preserving proposals**. They may prioritize or annotate a page, but
may not replace an original page, assign a final document group, overwrite a
value, or bypass the final client-review gate.

## Image preparation ensemble

Run `scripts/preprocess_pages.py` after immutable one-page intake. It retains
one grayscale master and produces sibling enhanced grayscale, global-180,
global-220, and adaptive-local-threshold views. OCR/HTR consumers compare their
outputs across the views; a difference is evidence for review, not a reason to
pick the prettiest result. The grayscale master remains the reference image.

## Handwriting detection and recognition

A free, local detector is useful only as a **triage** signal. Stroke morphology
and disagreement between binarization variants can identify likely annotation
regions, but neither is reliable enough to declare a page clean. Layout-delta
detection is deferred until an approved, versioned template exists for the
source layout; applying it to arbitrary business documents would create false
positives and false negatives.

The selected PDF-mode LLM extraction pass is the current vision-language (VLM)
confirmation route. It emits normalized-page-coordinate `handwriting_regions`
and review-only `handwriting_readings` proposals along with `has_handwriting`; it
must be run with `--input-mode pdf` when visual-region detection or transcription
is required. A model reading is routed to independent handwriting reconciliation
and client review. It is not an approved amendment. Handwritten financial values
retain the existing strict three-reader rule in `references/handwriting.md`.

## Unordered document reassembly

Default reassembly accepts a complete `PAGE X OF Y` sequence only when same type
and a common labeled identifier agree. The opt-in
`--broad-unordered-proposals` adds a lower-confidence candidate: pages of the
same type that share exactly one labeled identifier. It leaves pages in original
source order, labels the group `proposed_group_requires_order_and_client_review`,
and returns any additional identifier as an ambiguity exception. It never
creates a document ID or changes page provenance.

## LLM classification

The selected OpenAI, Gemini-on-Vertex, or isolated OpenRouter adapter is the
constrained classification-model lane. For an unclassified page it submits the
immutable one-page PDF when that provider/input mode supports it and emits a
strict `model_document_type` proposal. Rule-classified pages continue to use
native text in automatic mode for cost control. Any model proposal,
disagreement with a high-signal rule, failure, or unknown type is final-review
work; it is not a replacement for deterministic classification.

## Calibration and release

Before enabling a new detector, template comparison, or model configuration on
client production data, measure it against an approved representative golden
set by document type, source layout, image-quality band, handwriting presence,
and financial materiality. Record precision, recall, false-negative sampling,
and its configuration in the non-secret run artifact. Do not claim a numerical
accuracy rate without that evaluation.

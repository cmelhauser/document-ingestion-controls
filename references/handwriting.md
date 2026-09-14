# Handwriting Subsystem

Handwriting on business documents is rarely decorative. It is almost always
recording something that changed *after* the document was printed: a short
receipt, a price override, a cheque number, an approval, a delivery date, a
damage note.

Two failure modes, and the second is worse:

- **Ignoring it.** The printed invoice says 40 units; someone wrote "38 — two
  damaged". A pipeline that ignores annotations confidently reports 40.
- **Guessing at it.** Handwritten digits are genuinely ambiguous. A 4 and a 9 can
  be identical in one person's hand, as can 3/5/8, 0/6, and 1/7. An invoice for
  $4,000 recorded as $9,000 is a $5,000 error sitting silently in the books, and
  nothing downstream will flag it.

Hence the governing asymmetry: **handwriting gets a stricter acceptance threshold
than printed text, not a looser one.**

## Contents

- [Detection — Branch A](#detection--branch-a-colour-present)
- [Detection — Branch B](#detection--branch-b-grayscale-or-bilevel)
- [Branch B compensating controls](#branch-b-compensating-controls)
- [Selective rescan](#selective-rescan-branch-b-rescan)
- [Recognition](#recognition)
- [Semantic typing](#semantic-typing)
- [Precedence rule](#precedence-rule)
- [Signatures](#signatures)

---

## Detection — Branch A (colour present)

Three signals, in order of strength.

**1. Ink and colour separation.** Blue and black ballpoint, red marker, and
purple stamp ink separate cleanly from printed black in HSV space. This single
signal finds most annotation regions and is the strongest detector available.
Threshold on saturation with a low-value floor to avoid picking up scanner noise
and JPEG colour fringing around printed glyph edges.

**2. Stroke morphology.** Stroke-width variance, baseline instability, slant
irregularity, and curvature entropy separate handwriting from print where ink
colour matches — black pen on black printing.

**3. Layout delta.** Marks outside the printed form's known field grid, or
overlapping printed glyphs, are annotations by definition.

Expected region-detection recall: high. `has_handwriting = false` is reliable in
this branch.

---

## Detection — Branch B (grayscale or bilevel)

The strongest signal is gone. Rebuild from three weaker ones used together.

**1. Stroke morphology.** Works on bilevel. Becomes the primary detector.

**2. Layout delta.** Register each page against a learned per-vendor form
template; ink outside the printed field grid or overlapping printed glyphs is
annotation. Strong on standardized forms, weak on free-format documents.

**3. Full-page VLM annotation pass.** A vision-language model enumerates
handwritten regions with bounding boxes. Slower and more expensive than ink
separation but entirely colour-independent. **In Branch B this is mandatory on
every page**, not optional.

---

## Branch B compensating controls

Detection recall will be materially lower. Compensate structurally rather than
hoping.

**Assume handwriting is present until proven absent.** Branch A may treat
`has_handwriting = false` as reliable. Branch B may not. Every Branch B page
routes through the recognition path regardless of detector output — detection
sets processing *priority*, not *eligibility*.

**Lean on arithmetic self-proof.** An undetected handwritten quantity or price
override will usually break the document's internal arithmetic, and that failure
catches what detection missed. This is the principal safety net in Branch B and
the reason the self-proof rule is non-negotiable.

**Raise QA oversampling** from 3–5× to 8–10× on pages the detector calls clean
but which sit in vendor or period strata known to contain annotations.

**Escalate JBIG2 strata** regardless of confidence — see
`references/workflow-gates.md`, Phase 0.5.

---

## Selective rescan (Branch B-rescan)

If original paper survives, do not rescan everything. Rescan only:

- Pages the detector flags as annotated
- Pages belonging to documents that failed arithmetic self-proof
- Pages drawn into the QA sample strata

Typically 10–20% of the corpus. Rescan at 300 DPI, 24-bit colour, no lossy
compression. Those pages then process under Branch A while the rest stay on B.

This works because annotations concentrate in a minority of pages. Cost is a
fraction of a full rescan and it recovers most of the accuracy gap. Present it to
the client as a priced option against a stated accuracy delta, not as a
recommendation.

---

## Recognition

**Route handwriting to a handwriting-specific recognizer.** Printed-invoice
parsers do not fail gracefully on cursive — they emit plausible, wrong values at
high confidence. Use dedicated HTR (Azure `prebuilt-read`, Google Document AI)
run in parallel.

### Google Cloud Vision handwriting OCR lane

The implemented Google lane uses Cloud Vision `DOCUMENT_TEXT_DETECTION` with a
handwriting language hint. It submits every retained page, preserves the full
structural response, and binds returned words to normalized handwriting regions
produced by a separate visual detector. Cloud Vision is a recognizer here, not
the proof that a region is handwritten. A page with no supplied regions is
recorded as processed but is never declared handwriting-free.

The lane emits `google_cloud_vision_handwriting_htr_v1`, a non-secret adapter
handoff, raw responses, retained rendered-page images, and explicit provider or
unreadable-region exceptions. It is one `google` independence-group vote.
Another Google model, prompt, or service role cannot independently
validate it. Run `handwriting_review.py` with genuinely separate provider groups
and preserve all disagreements. The official service contract is documented in
[Google Cloud Vision handwriting OCR](https://docs.cloud.google.com/vision/docs/handwriting).

Every detector/extraction region must carry a page-stable `region_id` and
normalized page coordinates. The reading repeats that ID. This prevents a
transcription from being joined to a nearby mark by ordering or label alone.
The reconciler also accepts retained visual-extraction record lists directly;
it derives a conservative provider family from `engine` and never treats two
models from the same provider as independent evidence.

**Give the model the full page image plus surrounding printed context, not an
isolated crop.** Context is frequently what settles the reading: if a handwritten
quantity must multiply against a printed unit price to reach a printed total,
only one digit is arithmetically possible. A cropped region throws away exactly
the information that disambiguates it.

### Acceptance thresholds

| Content class | Threshold to auto-accept | Rationale |
|---|---|---|
| Handwritten numerics | **3-of-3 agreement** | Digit confusion pairs are non-recoverable errors in a financial dataset |
| Handwritten free text | 2-of-3 | Feeds classification and search, not arithmetic |
| Signatures | Never transcribed as data | See below |

Anything below threshold is an exception and is worked by a human. This will
generate more manual work than the printed path. That is the intended trade.

### Known confusion pairs

Flag these for elevated scrutiny whenever they appear in a numeric field:

`1/7` · `4/9` · `3/5/8` · `0/6` · crossed European 7 read as 1 · decimal comma vs
decimal point · thousands separator ambiguity (`1.234` meaning 1234 or 1.234) ·
trailing `/-` or `.00` shorthand

---

## Semantic typing

Classify each annotation by **function**, because function determines financial
precedence and destination table. A number is not just a number — where it sits
on the page tells you what it is.

| Annotation type | Effect on the record |
|---|---|
| Quantity correction / short receipt | Overrides printed quantity; creates `amendment` row; printed value retained |
| Price or total override | Overrides printed amount; **re-run arithmetic self-proof against the corrected figure** |
| Cheque number, payment date, "PAID" | Creates or links a `payment` record |
| Delivery date, receiver name | Populates POD fields; drives on-time and transit-time metrics |
| Signature / initials | Boolean `signed` + approver identity where legible; never OCR'd into a text field |
| Damage, shortage, refusal note | Creates `exception_event` linked to the shipment; drives claims analysis |
| GL code, cost centre, account coding | Populates coding fields for spend analytics |

Position heuristics: a number beside a printed quantity is a quantity correction;
a number near "PAID" or in a payment block is a cheque number; a date near a
signature line is a delivery date; free text in a margin is usually an exception
note.

---

## Precedence rule

State this explicitly in any implementation, because ambiguity here corrupts
financials directly:

> **Printed values are the baseline. Handwritten values are amendments carrying a
> later effective time. Both are stored.**

The canonical layer exposes the amended value as current and the printed value as
`original_value`, with `amendment_source = 'handwritten'`. Auditable and
reversible.

Silently overwriting either value is the failure mode to avoid. If the
handwriting was misread, an overwrite makes the error unrecoverable; storing both
makes it a correction.

**Any handwritten amendment changing a document total by more than the Phase 0
threshold is worked manually.** Recommend that threshold be zero — all of them.
There will not be many, and they matter disproportionately.

---

## Signatures

Never transcribe a signature into a text field as if it were data. A signature is
not a name — it is evidence that someone signed.

Record:

- `signed` (boolean)
- `signature_region` (bounding box, for retrieval)
- `approver_identity` — only where separately legible, e.g. a printed name line
  beneath, or initials matching a known approver list

Attempting to read cursive signatures as names produces a field full of confident
nonsense that then contaminates entity resolution.

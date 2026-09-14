"""Turn root-cause groups into the smallest set of questions a client can answer.

A review queue lists findings. A client cannot act on findings: 200 rows saying
``partial_disagreement`` are one question asked 200 times, and answering them one
at a time is how a review that should take an hour takes a week.

Each group produced by :mod:`client_review.grouping` is already a repeated
pattern -- one template family meeting one finding family -- so it corresponds to
exactly one decision. This module gives that decision a plain-language form: what
was found, why it matters, what we need back, and one worked example the client
can look at. Answering it settles every item behind it.

Two rules keep this honest. The question never proposes the answer, because a
batch rule the client did not choose is an approval this pipeline is not allowed
to make. And answering a question is kept strictly separate from clearing what it
covers: protected findings -- arithmetic, provider, handwriting, identity and the
rest named in rule 7 -- are still asked about, because the client's answer is
exactly what resolves them, but that answer is applied and every affected item
re-checked individually rather than closed in bulk.
"""

from __future__ import annotations

from typing import Any

# Ordered most-answerable first. The wording is deliberately plain: a client
# reading these is an accounts or operations person, not an auditor, and a
# question phrased in pipeline vocabulary gets answered wrongly or not at all.
QUESTION_TEMPLATES: dict[str, dict[str, Any]] = {
    # The bucket `grouping.disagreement_family` uses for a reason it has no entry
    # for. Wording it here keeps those items out of the generic question, which
    # is what 3,958 handwriting items once fell into. The findings underneath are
    # mixed by definition, so the question sends the client to the examples
    # rather than claiming to know what they have in common.
    "unclassified_finding": {
        "headline": "We found something on these pages we could not put a name to. What are they?",
        "what_we_found": (
            "These findings did not match any cause we recognise, so they were "
            "kept together rather than filed under a cause that would have been "
            "a guess. What they have in common is that we could not classify "
            "them, not that they are the same problem."
        ),
        "why_it_matters": (
            "An unclassified finding blocks its field the same way a named one "
            "does. Left unasked it would sit open with nothing to resolve it, "
            "and filing it under the wrong cause would send it to a check that "
            "was never meant for it."
        ),
        "what_we_need": (
            "Look at the example pages for this group and tell us what you see. "
            "If they are several different things, say so and we will split them "
            "and come back with the specifics."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "These are all the same thing -- I will explain in the note",
            "These are several different things -- split them and ask again",
            "These can be ignored",
        ),
    },
    # Both of these arrive from the handwriting lane and both reached clients as
    # "We need your confirmation on these items" -- 4,330 items on one run, shown
    # a page with no question attached. Neither is answerable as posed, so the
    # wording says what the finding is and what we intend to do about it.
    "duplicate_engine_region": {
        "headline": "We marked the same handwriting twice. Nothing for you to decide here.",
        "what_we_found": (
            "More than one reader outlined the same handwritten area on a page, so "
            "the same mark was counted as several findings. This is our "
            "book-keeping, not a question about your documents."
        ),
        "why_it_matters": (
            "Counted twice, one annotation looks like several unresolved items and "
            "inflates what is outstanding. Left alone it would keep a document open "
            "for a mark that was already dealt with."
        ),
        "what_we_need": (
            "Nothing. We collapse the duplicate outlines and keep the reading that "
            "carries evidence. Tell us only if you would rather see every reader's "
            "outline kept separately."
        ),
        "decision_choice": "Approve proposed routing",
        "answer_options": (
            "Collapse the duplicates -- that is fine",
            "Keep every reader's outline separately",
        ),
    },
    "missing_or_invalid_reading": {
        "headline": "We found handwriting here but could not read it. Can you tell us what it says?",
        "what_we_found": (
            "The handwriting reader located a mark on the page and returned no "
            "legible text for it. There is no reading to show you, so there is "
            "nothing here to confirm or correct."
        ),
        "why_it_matters": (
            "An unread mark is not the same as an absent one. Recorded as a blank "
            "it would look like a field nobody wrote in, when in fact something is "
            "written that we could not make out."
        ),
        "what_we_need": (
            "Look at the example page. If the mark carries a value or an "
            "instruction, tell us what it says. If it is a tick, an initial or a "
            "filing scribble, saying so lets us retain it as a comment and stop it "
            "holding the document open."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "It is a note or a mark, not a value",
            "It carries a value -- we will tell you what it says",
            "Ignore handwriting on documents of this kind",
        ),
    },
    "payment_settlement_grain": {
        "headline": "Your payments do not name the documents they pay. What does one payment cover?",
        "what_we_found": (
            "Every row in the payment export carries a date and an amount, and "
            "most carry no customer and no document number. We could not tell "
            "which document any payment settles, so no payment has been applied "
            "to any document."
        ),
        "why_it_matters": (
            "Whether a payment settles one document or many changes what the "
            "figures mean. If a payment is a periodic settlement across several "
            "statements, matching it to a single document would be wrong, and a "
            "document that looks unpaid may simply be part of a batch that was."
        ),
        "what_we_need": (
            "Tell us what one payment represents. If payments are periodic "
            "settlements, say roughly what period each covers. If your system can "
            "export payments with the document numbers they were applied to, that "
            "one file would resolve this entire group."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "Each payment settles one specific document",
            "A payment is a periodic settlement covering several documents",
            "It varies -- we will explain",
            "We can export payments with the documents they were applied to",
        ),
    },
    "single_reading": {
        "headline": "Only one of our two systems could read these values. Are they right?",
        "what_we_found": (
            "We read every document with two independent systems and keep a value "
            "only when both agree. On these, one system returned a value and the "
            "other did not read the field at all, so there is nothing to check it "
            "against."
        ),
        "why_it_matters": (
            "A value only one system read has no independent confirmation. We hold "
            "it rather than accept it, because accepting it would present a single "
            "machine reading as a verified figure."
        ),
        "what_we_need": (
            "Look at the example page and tell us whether the value we show is "
            "right. If this kind of field is normally blank on these documents, "
            "say so -- that answers it for all of them."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "The value shown is correct",
            "The value shown is wrong -- we will supply the correct one",
            "This field is normally blank on documents of this kind",
            "Apply a rule for all documents of this kind (describe it)",
        ),
    },
    "provider_disagreement": {
        "headline": "Two independent readings disagreed about the same values. Which is right?",
        "what_we_found": (
            "We read every document twice, with two different systems. On these "
            "documents the two readings did not match."
        ),
        "why_it_matters": (
            "We do not accept a value that only one reading supports, so these "
            "stay unresolved until someone who knows the documents confirms them."
        ),
        "what_we_need": (
            "The example shows both values and the page they came from. Tell us "
            "which one matches the paperwork, or give us the rule we should apply "
            "for documents of this kind."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "The first value shown in the example is correct",
            "The second value shown in the example is correct",
            "Neither is correct -- we will supply the value",
            "Apply a rule for all documents of this kind (describe it)",
        ),
    },
    "document_type_proposal": {
        "headline": "We are not certain what kind of document these are. Can you confirm?",
        "what_we_found": (
            "These documents did not clearly match a known document type, so we "
            "left the type open rather than guessing."
        ),
        "why_it_matters": (
            "The document type decides which checks run and which fields are "
            "expected, so guessing it wrongly would send the whole record down "
            "the wrong path."
        ),
        "what_we_need": "Tell us what these documents are called in your business.",
        "decision_choice": "Confirm document family/type",
        "answer_options": (),
    },
    "document_type_disagrees_with_intake_rule": {
        "headline": "Our reading systems disagree with our own filing rule about what these are.",
        "what_we_found": (
            "We sort documents by a rule first, then read them. On these, what we "
            "read did not match what the rule filed them as."
        ),
        "why_it_matters": (
            "The document type decides which checks run and which fields are "
            "expected. We would rather ask than let a mis-filed document be "
            "checked against the wrong expectations."
        ),
        "what_we_need": (
            "Look at the example page and tell us what this document is called in your business."
        ),
        "decision_choice": "Confirm document family/type",
        "answer_options": (
            "The type we read is correct",
            "The type our rule assigned is correct",
            "Neither -- we will tell you what it is",
        ),
    },
    "provider_extraction_failure": {
        "headline": "Our reading system failed on these pages and returned nothing.",
        "what_we_found": (
            "The reading system returned an error rather than a reading for "
            "these pages, so no values were taken from them. This is our system "
            "failing, not a judgement about your paperwork."
        ),
        "why_it_matters": (
            "A page we could not read is not a page with no data on it. Leaving "
            "it out silently would understate your totals."
        ),
        "what_we_need": (
            "Usually nothing -- we re-read these ourselves first. Tell us only if "
            "these pages should be left out of the dataset on purpose, or if a "
            "better copy exists that we should use instead."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "Re-read them; nothing is wrong with the pages",
            "A better scan or original exists -- we will send it",
            "Exclude these pages deliberately",
            "Key them in manually",
        ),
    },
    "arithmetic_or_reassembly": {
        "headline": "The figures on these documents do not add up. Which figure governs?",
        "what_we_found": (
            "The line items and the printed totals on these documents disagree with each other."
        ),
        "why_it_matters": (
            "We never adjust a figure to make a document balance. Either the "
            "printed total governs, or the lines do, and only you can say which."
        ),
        "what_we_need": "Tell us which figure is authoritative on documents of this kind.",
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "The printed total governs",
            "The line items govern",
            "The document is genuinely wrong -- treat as an exception",
        ),
    },
    "provider_or_schema_exception": {
        "headline": "These readings did not fit the expected shape. Can you confirm the layout?",
        "what_we_found": (
            "The values came back in a form we could not match to the expected "
            "fields for this kind of document."
        ),
        "why_it_matters": (
            "Forcing an unfamiliar layout into familiar fields is how data ends "
            "up in the wrong column without anyone noticing."
        ),
        "what_we_need": "Confirm what the columns on these documents mean.",
        "decision_choice": "Approve proposed routing",
        "answer_options": (),
    },
    "adjudication_ambiguity": {
        "headline": "More than one correction would fit. Which one is intended?",
        "what_we_found": (
            "We found several equally supported ways to correct these values and "
            "would not choose between them."
        ),
        "why_it_matters": "Picking one on your behalf would be a guess recorded as a fact.",
        "what_we_need": "Tell us which correction is intended.",
        "decision_choice": "Provide alternative in comment",
        "answer_options": (),
    },
    "validation_exception_family": {
        "headline": "Some values look implausible. Are they real?",
        "what_we_found": (
            "These values are outside the range we would expect -- for example a "
            "negative amount or an unexpected currency."
        ),
        "why_it_matters": (
            "An implausible value is usually a misread figure, but sometimes it "
            "is genuinely what the document says. We do not assume either."
        ),
        "what_we_need": "Confirm whether these values are correct as printed.",
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "Correct as printed",
            "Misread -- we will supply the correct value",
        ),
    },
    "provider_review_flag_family": {
        "headline": "The reading system flagged these itself. Can you confirm them?",
        "what_we_found": (
            "The system that read these pages raised its own concern about what "
            "it saw, and we kept the flag rather than discarding it."
        ),
        "why_it_matters": "A reader that doubts its own output is worth listening to.",
        "what_we_need": "Confirm the values on the example page.",
        "decision_choice": "Provide alternative in comment",
        "answer_options": (),
    },
    "missing_or_conflicting_semantic_type": {
        "headline": "There is handwriting on these pages and we cannot tell what it is meant to do.",
        "what_we_found": (
            "We read the handwriting and can show you what it says. What we cannot "
            "tell from the page alone is what it is for -- whether a figure written "
            "beside a printed one replaces it, comments on it, or refers to "
            "something else entirely."
        ),
        "why_it_matters": (
            "A handwritten number next to a printed number is either a correction "
            "or a note, and the two lead to opposite figures. We will not guess "
            "which, so the printed value stands and the handwriting is held here."
        ),
        "what_we_need": (
            "Look at the example page and tell us what handwriting of this kind "
            "means on your documents. If there is a house convention -- a circle "
            "means query, a figure in the margin means the corrected amount -- give "
            "us that and it answers all of them."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "The handwriting replaces the printed value",
            "The handwriting is a note and the printed value stands",
            "It depends on the mark -- we will describe our convention",
            "Ignore handwriting on documents of this kind",
        ),
    },
    "google_handwriting_region_unreadable": {
        "headline": "There is handwriting on these pages we could not read at all.",
        "what_we_found": (
            "Our reader found handwriting in these places and could not make out "
            "what it says. We have kept the page and the exact spot on it, but we "
            "have no reading to show you."
        ),
        "why_it_matters": (
            "We would rather tell you a mark is unreadable than offer a guess at "
            "it. Left unanswered these are simply unknown, and if any of them "
            "carry money that matters."
        ),
        "what_we_need": (
            "Look at the marked spot on the example page and tell us what it says, "
            "or tell us that handwriting there does not matter on documents of this "
            "kind."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "We can read it -- we will supply the text",
            "It is illegible to us too",
            "Handwriting in this position does not matter on these documents",
            "A better copy of these pages exists",
        ),
    },
    "insufficient_independent_agreement": {
        "headline": "Only one system read this handwriting, so nothing confirms it.",
        "what_we_found": (
            "Handwriting is read by one provider group, and one group is one "
            "opinion. Where a second could not confirm the reading, we hold it."
        ),
        "why_it_matters": (
            "Handwritten digits are where reading goes wrong most often -- 1 and 7, "
            "4 and 9, 3, 5 and 8 -- and an unconfirmed digit in a money column is "
            "not an error anything downstream would catch."
        ),
        "what_we_need": (
            "Look at the example and tell us whether the reading we show is what the page says."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "The reading shown is correct",
            "The reading shown is wrong -- we will supply the correct one",
            "The mark is not a value and should not be read as one",
        ),
    },
    "unresolved_source_template_mapping": {
        "headline": "We could not tell which of your columns these values belong to.",
        "what_we_found": (
            "We matched most columns on these documents to a field. On these we "
            "could not, so the values are read but not filed anywhere."
        ),
        "why_it_matters": (
            "A value in the wrong column is worse than a missing one: it is wrong "
            "and it looks right. We would rather leave it unfiled and ask."
        ),
        "what_we_need": (
            "Look at the column heading on the example page and tell us what it "
            "means in your business."
        ),
        "decision_choice": "Approve proposed routing",
        "answer_options": (
            "We will name the field this column holds",
            "This column is not needed -- ignore it",
            "The heading changed part-way through the period (describe when)",
        ),
    },
    "refinement_source_value_differs": {
        "headline": "A second pass over the same table read a value differently.",
        "what_we_found": (
            "We re-read these tables to improve them, and on these cells the second "
            "reading did not match the first."
        ),
        "why_it_matters": (
            "The later reading is not automatically the better one, and we will not "
            "silently replace a value with its own re-reading."
        ),
        "what_we_need": (
            "The example shows both readings and the cell they came from. Tell us "
            "which matches the page."
        ),
        "decision_choice": "Provide alternative in comment",
        "answer_options": (
            "The first reading is correct",
            "The second reading is correct",
            "Neither is correct -- we will supply the value",
        ),
    },
    # The nine concerns `grouping.audit_concern` sorts table-audit findings
    # into, plus its fallback. Before this, each audit sentence was its own
    # family and its own question: 11,251 findings produced 4,455 groups of one.
    "audit_totals_or_to_date_row": {
        "decision_choice": "Provide alternative in comment",
        "headline": "Which total governs when a page shows more than one?",
        "what_we_found": (
            "These pages carry a total on the line items and a second running "
            "total -- a subtotal, a page total, or a 'to date' row. Both are "
            "printed, and they do not always agree."
        ),
        "why_it_matters": (
            "Taking the wrong one changes the figure for the whole document, and "
            "a running total counted as a line total double-counts."
        ),
        "what_we_need": (
            "Tell us which figure governs on documents of this kind, and whether "
            "the running total should be ignored."
        ),
        "answer_options": (
            "The line items govern; ignore the running total",
            "The printed total governs",
            "It depends -- we will describe the rule",
        ),
    },
    "audit_handwriting_present": {
        "decision_choice": "Provide alternative in comment",
        "headline": "There is handwriting on these pages. Does it change the printed figure?",
        "what_we_found": (
            "Our audit found handwriting beside or over the printed values on "
            "these pages -- marks, circles, or written figures."
        ),
        "why_it_matters": (
            "A handwritten number next to a printed one is either a correction or "
            "a note, and the two give opposite answers. We do not guess, so the "
            "printed value stands and the handwriting is held here."
        ),
        "what_we_need": ("Tell us what handwriting of this kind means on your documents."),
        "answer_options": (
            "The handwriting is a note and the printed value stands",
            "The handwriting replaces the printed value",
            "It depends on the mark -- we will describe our convention",
        ),
    },
    "audit_value_clipped_or_obscured": {
        "decision_choice": "Provide alternative in comment",
        "headline": "Part of the value is cut off or covered on these pages.",
        "what_we_found": (
            "The value runs past the edge of its column, off the page, or is "
            "covered by a stamp or mark, so we can see only part of it."
        ),
        "why_it_matters": (
            "Half of a number read confidently is still the wrong number, and "
            "nothing downstream would catch it."
        ),
        "what_we_need": (
            "Tell us the full value, or whether a complete copy of these pages "
            "exists that we should use instead."
        ),
        "answer_options": (
            "We will supply the full values",
            "A complete copy exists -- we will send it",
            "The visible part is the whole value",
        ),
    },
    "audit_no_header_or_label": {
        "decision_choice": "Approve proposed routing",
        "headline": "These columns have no heading we can read. What do they hold?",
        "what_we_found": (
            "The column or row carries values but no heading, or a heading we "
            "could not make out, so we cannot say which field it belongs to."
        ),
        "why_it_matters": (
            "A value filed under the wrong field is worse than a missing one: it "
            "is wrong and it looks right."
        ),
        "what_we_need": ("Look at the example pages and tell us what these columns hold."),
        "answer_options": (
            "We will name the fields these columns hold",
            "These columns are not needed -- ignore them",
            "The heading changed part-way through the period",
        ),
    },
    "audit_sources_conflict": {
        "decision_choice": "Provide alternative in comment",
        "headline": "Our two readings of these cells disagree. Which is right?",
        "what_we_found": (
            "The page and our independent reading of it do not agree on these values."
        ),
        "why_it_matters": (
            "We do not accept a value only one reading supports, so these stay "
            "unresolved until someone who knows the paperwork settles them."
        ),
        "what_we_need": (
            "Tell us which reading matches the paperwork, or the rule we should "
            "apply for documents of this kind."
        ),
        "answer_options": (
            "The value shown on the page is correct",
            "Neither is correct -- we will supply the value",
            "Apply a rule for all documents of this kind (describe it)",
        ),
    },
    "audit_field_association_unclear": {
        "decision_choice": "Approve proposed routing",
        "headline": "We cannot tell which row or field these values belong to.",
        "what_we_found": (
            "The value is legible, but its position does not make clear which row "
            "or which field it belongs with."
        ),
        "why_it_matters": (
            "A correct value attached to the wrong row is a misattribution, and "
            "page-level checks will not catch it."
        ),
        "what_we_need": (
            "Tell us how these documents are meant to be read -- which value goes with which row."
        ),
        "answer_options": (
            "We will describe how to read these",
            "The value applies to the whole document, not one row",
            "Ignore these values",
        ),
    },
    "audit_page_populated_contrary_to_concern": {
        "decision_choice": "Approve proposed routing",
        "headline": "We flagged these pages, then found they are populated after all.",
        "what_we_found": (
            "An earlier check raised a concern about these pages, and the audit "
            "then found the content it said was missing."
        ),
        "why_it_matters": (
            "This is usually our own false alarm rather than a problem with your "
            "paperwork, but we will not close our own finding without saying so."
        ),
        "what_we_need": (
            "Usually nothing. Tell us only if these pages should be excluded on purpose."
        ),
        "answer_options": (
            "Nothing is wrong with these pages -- proceed",
            "Exclude these pages deliberately",
        ),
    },
    "audit_agreement_does_not_resolve": {
        "decision_choice": "Provide alternative in comment",
        "headline": "Both our readings agree, and we still cannot tell what it means.",
        "what_we_found": (
            "Our two readings of these cells match, so the characters are not in "
            "doubt. What the value means in your business is."
        ),
        "why_it_matters": (
            "Agreement about what is printed is not agreement about what it is "
            "for, and only you can settle the second."
        ),
        "what_we_need": ("Look at the example pages and tell us what these values mean here."),
        "answer_options": (
            "We will explain what these mean",
            "Apply a rule for all documents of this kind (describe it)",
            "Ignore these values",
        ),
    },
    "audit_value_not_visible": {
        "decision_choice": "Provide alternative in comment",
        "headline": "We expected a value on these pages and cannot find one.",
        "what_we_found": (
            "A field we would expect on a document of this kind is not visible "
            "anywhere on the page."
        ),
        "why_it_matters": (
            "A missing value and a value we failed to read are different, and "
            "only one of them is a gap in your records."
        ),
        "what_we_need": ("Tell us whether this field is simply not used on these documents."),
        "answer_options": (
            "This field is normally blank on documents of this kind",
            "It should be there -- we will supply it",
            "A better copy exists -- we will send it",
        ),
    },
    "audit_unclassified": {
        "decision_choice": "Provide alternative in comment",
        "headline": "Our audit raised a mixed set of questions about these pages.",
        "what_we_found": (
            "These findings did not fall into any of the recurring patterns, so "
            "they are grouped together and each carries its own note."
        ),
        "why_it_matters": (
            "They are held rather than guessed at, and the underlying notes are "
            "retained in full on the queue tab."
        ),
        "what_we_need": (
            "Read the notes for this group on the queue tab and tell us how to treat them."
        ),
        "answer_options": (
            "We will answer these individually",
            "Apply a rule for all of them (describe it)",
        ),
    },
    "document_wrapper": {
        "headline": "These documents are held open by findings listed elsewhere.",
        "what_we_found": (
            "The document as a whole is still open because one or more of the "
            "questions in this pack applies to it."
        ),
        "why_it_matters": "It will close on its own once those questions are answered.",
        "what_we_need": "No separate answer needed -- answer the other questions first.",
        "decision_choice": "Internal only — no client choice",
        "answer_options": (),
    },
}
DEFAULT_TEMPLATE: dict[str, Any] = {
    "decision_choice": "Provide alternative in comment",
    "headline": "We need your confirmation on these items.",
    "what_we_found": "These items could not be resolved from the documents alone.",
    "why_it_matters": (
        "We keep every unresolved item visible rather than closing it on an assumption."
    ),
    "what_we_need": "Review the example page and confirm what is correct.",
    "answer_options": (),
}
# Rule 7 forbids a batch decision *clearing* arithmetic, provider/schema,
# reassembly, handwriting, identity, review-flag, missing-field, or
# unresolved-evidence findings. It does not forbid asking about them, and asking
# is where nearly all the value is: on a measured corpus one question about two
# confusable columns informed 751 items. So a protected group still gets a real
# question and real options; what it never gets is the claim that answering it
# closes anything. The answer is applied and every affected item is then re-checked
# individually.
BATCH_CLEAR_CAVEAT = (
    "Your answer will be applied and every affected item re-checked one by one. "
    "It will not close them automatically -- findings of this kind are never "
    "cleared in bulk."
)
BATCH_CLEAR_NOTE = "Answering this settles the whole group once applied."
# The wrapper family is the one group with nothing to ask: it closes when the
# other questions are answered.
SELF_RESOLVING_FAMILIES = frozenset({"document_wrapper"})
PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2}


def template_for(finding_family: str) -> dict[str, Any]:
    """Return the plain-language wording for one finding family."""
    return QUESTION_TEMPLATES.get(finding_family, DEFAULT_TEMPLATE)


def group_priority(group: dict[str, Any], items: list[dict[str, Any]]) -> str:
    """Take the most urgent priority any member item carried."""
    best = "normal"
    for item in items:
        priority = str(item.get("priority", "normal")).casefold()
        if PRIORITY_ORDER.get(priority, 2) < PRIORITY_ORDER.get(best, 2):
            best = priority
    return best


# How many worked examples a question shows. One was not enough and the cost of
# that was concrete: a question covering 285 documents showed the group's
# first page, which happened to be blank, and the client answered "Blank Page.
# Can be ignored." for all of them. The other 284 carried a median of 91
# populated fields and up to 701. Applying that answer would have discarded
# 3,043 findings across 278 dense commission reports.
EXAMPLE_LIMIT = 3


def examples_for(
    items: list[dict[str, Any]],
    limit: int = EXAMPLE_LIMIT,
    uninformative: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Choose worked examples spread across the group, not clustered at its start.

    The examples are taken at even intervals through the ordered members rather
    than off the top, because a group is only worth one example when its members
    are alike, and nothing here establishes that they are.

    ``uninformative`` names pages that cannot answer a question about content --
    in practice the near-blank ones scan profiling already flags. A client shown
    a blank page and asked to confirm a layout answers about the blank page, and
    they are right to. That happened: a question covering 285 documents led with
    the group's first page, which was blank, and the answer "Blank Page. Can be
    ignored." was given for all 285 and reaffirmed twice. Only 4 of those 285
    were blank; 194 carried more than fifty populated fields.

    Such a page is moved to the back rather than dropped, because a group that is
    genuinely all blank still has to show one.
    """
    if limit < 1:
        raise ValueError("example limit must be positive")
    ordered = _ordered_items(items)
    if not ordered:
        return [example_for([])]
    documents, seen = [], set()
    for item in ordered:
        key = str(item.get("document_id") or item.get("page_id") or "")
        if key not in seen:
            seen.add(key)
            documents.append(item)
    informative = [
        item
        for item in documents
        if str(item.get("page_id") or item.get("document_id") or "") not in uninformative
    ]
    # Only fall back to the uninformative ones when there is nothing else.
    documents = informative or documents
    if len(documents) <= limit:
        chosen = documents
    else:
        step = (len(documents) - 1) / (limit - 1) if limit > 1 else 0
        chosen = [documents[round(index * step)] for index in range(limit)]
    return [example_for([item]) for item in chosen]


def _ordered_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order members so an item naming a real page is preferred."""
    return sorted(
        items,
        key=lambda item: (
            not (item.get("page_id") or item.get("document_id")),
            str(item.get("document_id") or ""),
            str(item.get("field") or ""),
        ),
    )


def example_for(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose one worked example, preferring an item that names a real page.

    A question whose example has no page behind it cannot be checked against the
    source, which is most of what makes an example useful, so an item carrying a
    page identifier wins over one that does not.
    """
    ordered = _ordered_items(items)
    chosen = ordered[0] if ordered else {}
    # The values each system read are what make the question answerable. Without
    # them a client is asked which of two readings is correct while being shown
    # neither, and a single-reading question shows no value to confirm at all.
    candidates = chosen.get("candidates")
    values = [str(item) for item in candidates if str(item)] if isinstance(candidates, list) else []
    return {
        "document_id": str(chosen.get("document_id") or ""),
        "page_id": str(chosen.get("page_id") or chosen.get("document_id") or ""),
        "field": str(chosen.get("field") or ""),
        "reason": str(chosen.get("reason") or ""),
        "region_id": str(chosen.get("region_id") or ""),
        "values_read": values,
    }


# How many field names and document ids a question names outright before it
# points at the workbook instead. A client reading a question needs to know what
# it is about; a list of four hundred ids is not that.
NAMED_LIMIT = 12
READINGS_LIMIT = 6


def readings_summary(examples: list[dict[str, Any]]) -> str:
    """Concatenate what the engines actually read, for the cell beside the answer.

    Answering a value disagreement means choosing between readings, so the client
    needs those readings in the row where they type. They are carried on every
    example already, but reached only the instruction PDF: the workbook asked
    which reading was right while showing neither on the line being answered.

    A group is homogeneous, so distinct values across its examples are what
    matter, not one example's copy of them.
    """
    seen: list[str] = []
    for example in examples:
        for value in example.get("values_read") or []:
            text = str(value).strip()
            if text and text not in seen:
                seen.append(text)
    if not seen:
        return ""
    summary = " | ".join(seen[:READINGS_LIMIT])
    if len(seen) > READINGS_LIMIT:
        summary += f" | (+{len(seen) - READINGS_LIMIT} more)"
    return summary


def readable_fields(field_families: list[Any]) -> str:
    """Name the fields a question is about, in the client's own vocabulary.

    The pack asked "Confirm what the columns on these documents mean" and then
    named no column. Everything needed was already on the question --
    `field_families` carries the captions the mapping lanes derived from the
    documents themselves -- and neither the workbook nor the PDF rendered it.
    """
    names = [str(name) for name in field_families if str(name).strip()]
    if not names:
        return ""
    shown = [name.replace("mapping_", "").replace("_", " ") for name in names[:NAMED_LIMIT]]
    more = len(names) - len(shown)
    return ", ".join(shown) + (f", and {more} more" if more > 0 else "")


def build_questions(
    groups: list[dict[str, Any]],
    items: list[dict[str, Any]],
    max_questions: int | None = None,
    uninformative: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Build the ordered question set and report exactly what it covers.

    Questions are ordered by how many items each one settles, so a client who
    answers only the first few still clears the largest share of the queue. When
    ``max_questions`` truncates the list the remainder is reported rather than
    dropped: a shorter pack is a smaller ask, never a smaller queue.
    """
    if max_questions is not None and max_questions < 1:
        raise ValueError("max_questions must be a positive integer")
    ordered = sorted(
        groups,
        key=lambda group: (
            bool(group.get("protected")),
            -int(group.get("item_count", 0)),
            str(group.get("group_id", "")),
        ),
    )
    questions: list[dict[str, Any]] = []
    for number, group in enumerate(ordered, 1):
        members = [
            items[index]
            for index in group.get("source_item_indexes", [])
            if isinstance(index, int) and 0 <= index < len(items)
        ]
        family = str(group.get("finding_family", ""))
        template = template_for(family)
        generic = family not in QUESTION_TEMPLATES
        is_protected = bool(group.get("protected"))
        self_resolving = family in SELF_RESOLVING_FAMILIES
        answerable = not self_resolving
        examples = examples_for(members, uninformative=uninformative)
        questions.append(
            {
                "question_id": f"Q{number:03d}",
                "group_id": group.get("group_id"),
                "template_family": group.get("template_family"),
                "finding_family": group.get("finding_family"),
                "field_families": group.get("field_families", []),
                "headline": template["headline"],
                "what_we_found": template["what_we_found"],
                "why_it_matters": template["why_it_matters"],
                "what_we_need": template["what_we_need"],
                "how_your_answer_is_used": (
                    BATCH_CLEAR_CAVEAT if is_protected else BATCH_CLEAR_NOTE
                ),
                "answer_options": [] if self_resolving else list(template["answer_options"]),
                "resolves_item_count": int(group.get("item_count", 0)),
                "document_count": int(group.get("document_count", 0)),
                "document_ids": group.get("document_ids", []),
                "priority": group_priority(group, members),
                "protected": is_protected,
                "client_answer_permitted": answerable,
                # Rule 7: a protected finding is never cleared by a batch answer.
                "batch_clear_permitted": answerable and not is_protected,
                "example": examples[0],
                "examples": examples,
                "representative_reasons": group.get("representative_reasons", []),
                "source_item_indexes": group.get("source_item_indexes", []),
                "all_source_items_retained": True,
                # A question this module has no wording for still gets asked,
                # but it is asked generically -- the client sees a page and is
                # told only to confirm it. That is worth naming rather than
                # leaving to be noticed: restoring the handwriting findings to
                # the gate put 3,958 items behind "We need your confirmation on
                # these items", because `disagreement_family` falls back to the
                # raw reason and two large families had no entry here.
                "generic_wording": generic,
                # Which `client_decision_compile.py` choice this question's
                # answers constitute. Without it there is no path from a
                # returned workbook to an authorized change: that command
                # accepts four fixed `decision_choice` values, this module
                # offered clients thirty-eight of its own, and the two sets did
                # not share a single string. A client could answer every
                # question correctly and none of it could be compiled.
                "decision_choice": template["decision_choice"],
                # What the question is actually about. Without these the client
                # is asked to confirm "these documents" and "the columns" with
                # nothing naming either.
                "what_we_think_these_are": group.get("template_family"),
                # The values on record, beside the box where the answer is typed.
                "what_we_read": readings_summary(examples),
                "fields_in_question": readable_fields(group.get("field_families", [])),
                "documents_in_question": list(group.get("document_ids", []))[:NAMED_LIMIT],
                "documents_in_question_total": int(group.get("document_count", 0)),
            }
        )
    total_items = len(items)
    included = questions if max_questions is None else questions[:max_questions]
    deferred = [] if max_questions is None else questions[max_questions:]
    covered = sum(question["resolves_item_count"] for question in included)
    answerable = [question for question in included if question["client_answer_permitted"]]
    batch_clearing = [question for question in included if question["batch_clear_permitted"]]
    return {
        "schema_version": "1.0",
        "summary": {
            "question_count": len(included),
            "answerable_question_count": len(answerable),
            "protected_question_count": sum(1 for q in included if q["protected"]),
            "batch_clearing_question_count": len(batch_clearing),
            "individually_rechecked_question_count": len(answerable) - len(batch_clearing),
            "deferred_question_count": len(deferred),
            "queue_item_count": total_items,
            "items_covered": covered,
            "coverage_pct": round(100.0 * covered / total_items, 2) if total_items else 0.0,
            "items_per_question": round(covered / len(included), 2) if included else 0.0,
            # How much of the ask this module could only phrase generically.
            # Add wording for the families named here before sending the pack.
            "generic_wording_question_count": sum(1 for q in included if q["generic_wording"]),
            "items_behind_generic_wording": sum(
                q["resolves_item_count"] for q in included if q["generic_wording"]
            ),
            "families_without_wording": sorted(
                {q["finding_family"] for q in included if q["generic_wording"]}
            ),
            "decision_mode": "proposal_only",
            "client_approval_required": True,
        },
        "questions": included,
        "deferred_questions": deferred,
    }

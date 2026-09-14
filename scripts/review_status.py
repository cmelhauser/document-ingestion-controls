#!/usr/bin/env python3
"""The one list of review statuses that count as clear.

Three controls carried their own copy of this list and one of them disagreed.
`canonical_export.py` admits `auto_accepted`, `sampled_verified` and
`exception_resolved`; `client_review/cross_record.py` agreed; the final review
queue builder in `client_review/queue.py` filed an open review item for every
status except `auto_accepted`.

That disagreement closed a loop. A document marked `sampled_verified` or
`exception_resolved` was given an open review item by the queue builder, and
the canonical export then refused the document *because* it had an open review
item -- refusing it for being reviewed. Neither status could ever reach
canonical, however much work was done to earn it, and the exclusion artifact
blamed the review gate rather than the disagreement.

A status list that decides what may be published belongs in one place.
"""

# `clear` is not a review status. It is the value `field_validation_status`
# carries when validation found nothing, and the queue builder reads that field
# as a fallback, so it is admitted there and nowhere else.
REVIEW_CLEAR = ("auto_accepted", "sampled_verified", "exception_resolved")

# The arithmetic statuses that do not block. The same loop, one control over:
# the canonical export admitted `not_applicable` while the queue builder filed an
# item for every status except `proved`, so a document whose arithmetic was
# established as inapplicable was refused for holding an arithmetic item.
# `not_provable` is absent deliberately: the arithmetic control reports it as
# "not a passing one; treat as an exception", and only an operator's named
# authorization relabels it (`arithmetic_check.py --not-provable-as-not-applicable`).
ARITHMETIC_CLEAR = ("proved", "not_applicable")

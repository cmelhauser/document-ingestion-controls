"""The client review lane: one place that turns pipeline findings into client questions.

Every control in this pipeline can block, and each one used to speak its own
dialect: an exception list here, a proposal artifact there, a review queue
somewhere else. A client faced with that gets a list of fields rather than a set
of questions, and answers each repeated symptom separately.

This package holds the whole path from a blocked control to a document a client
can actually answer: protection classification, the exhaustive queue, root-cause
grouping, consolidation, the minimal question set, the rendered workbook and
instruction PDF, and every reasoning lane that proposes review work -- the card,
inference, iterative, cross-record, cross-packet, exception-resolution, consensus,
simulated, and full-dataset agent lanes.

Keeping it together is the point: a reviewer's safety depends on grouping,
reduction, rendering, and every proposal lane agreeing about what a protected
finding is and what a review packet looks like, and they cannot agree while they
live in separate scripts that each decide for themselves.

Each lane keeps its own command-line entry point at the path it always had, so no
runbook, skill, or operator habit changes. The entry points are thin: they parse
nothing and decide nothing, they delegate to the module here.

Nothing here approves anything. A group is a proposed batch rule, a question is a
request for a client decision, and the exhaustive queue remains the source of
truth behind both.
"""

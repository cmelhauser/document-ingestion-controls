#!/usr/bin/env python3
"""Command-line entry point for the exhaustive final client-review queue.

The logic lives in :mod:`client_review.queue`, beside the grouping and rendering
that consume it. This file stays so existing runbooks, skills, and operator
habits keep working against the same path.
"""

from client_review.queue import main

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Command-line entry point for the hash-bound client comment context loader.

The logic lives in :mod:`client_review.context`, beside the other client-review
lanes it shares protection, grouping, and packet conventions with. This file
stays so existing runbooks, skills, and operator habits keep working against the
same path.
"""

from client_review.context import main

if __name__ == "__main__":
    main()

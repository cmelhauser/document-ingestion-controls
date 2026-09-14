#!/usr/bin/env python3
"""Command-line entry point for root-cause review grouping.

The logic lives in :mod:`client_review.grouping`, beside the reduction and
rendering that depend on it. This file stays so existing runbooks, skills, and
operator habits keep working against the same path.
"""

import sys

from client_review.grouping import main

if __name__ == "__main__":
    sys.exit(main())

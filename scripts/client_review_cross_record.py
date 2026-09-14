#!/usr/bin/env python3
"""Command-line entry point for the cross-record client-review lane.

The logic lives in :mod:`client_review.cross_record`, beside the other
client-review lanes it shares protection, grouping, and packet conventions with.
This file stays so existing runbooks, skills, and operator habits keep working
against the same path.
"""

import json
import sys

from client_review.cross_record import main

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Cross-record client review failed: {exc}")

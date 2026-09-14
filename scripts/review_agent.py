#!/usr/bin/env python3
"""Command-line entry point for the bounded full-dataset review agent.

The logic lives in :mod:`client_review.agent`, beside the other client-review
lanes it shares protection, grouping, and packet conventions with. This file
stays so existing runbooks, skills, and operator habits keep working against the
same path.
"""

import json
import sys

from client_review.agent import main

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Full-dataset review agent failed: {exc}")

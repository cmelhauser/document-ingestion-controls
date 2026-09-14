#!/usr/bin/env python3
"""Command-line entry point for the client-review consensus lane.

The logic lives in :mod:`client_review.consensus_review`, beside the other client-review
lanes it shares protection, grouping, and packet conventions with. This file
stays so existing runbooks, skills, and operator habits keep working against the
same path.
"""

from client_review.consensus_review import main

if __name__ == "__main__":
    main()

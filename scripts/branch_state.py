#!/usr/bin/env python3
"""Determine whether a branch's work is already on the main branch.

`main` requires linear history, so it is merged by squash or rebase and a fully
merged branch is **not** an ancestor of it. `git branch --merged` therefore
reports merged work as unmerged and cannot be used to decide whether a branch is
safe to delete, which is how a large backlog of indistinguishable refs
accumulates.

This resolves each branch by evidence instead:

  merged_by_ancestry  the tip is reachable from main
  merged_by_patch     `git cherry` finds no unapplied commits
  merged_by_subject   the tip's subject appears in main's history
  unresolved          none of the above -- inspect it by hand, never delete it

Reporting only. It never deletes a branch; it tells an operator which ones the
documented procedure in BRANCHING.md permits deleting.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from cli_help import apply_shared_help


def run_git(*args, cwd=None):
    """Run one read-only git command and return its completed process."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],  # noqa: S607 - git is resolved from PATH like every other tool here
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def git(*args, cwd=None):
    """Run one read-only git command and return its stdout."""
    completed = run_git(*args, cwd=cwd)
    if completed.returncode and completed.stderr.strip():
        raise ValueError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def local_branches(main, cwd=None):
    """Return every local branch except the main branch itself."""
    listing = git("for-each-ref", "--format=%(refname:short)", "refs/heads/", cwd=cwd)
    return [name for name in listing.split() if name and name != main]


def main_subjects(main, cwd=None):
    """Return the set of commit subjects already on the main branch."""
    return {
        line.strip()
        for line in git("log", "--format=%s", main, cwd=cwd).splitlines()
        if line.strip()
    }


def normalize_subject(subject):
    """Drop a trailing squash-merge PR reference so subjects compare equal."""
    stripped = subject.strip()
    if stripped.endswith(")") and " (#" in stripped:
        stripped = stripped[: stripped.rindex(" (#")]
    return stripped.strip()


def classify_branch(branch, main, subjects, cwd=None):
    """Resolve one branch against the main branch by the strongest evidence available."""
    if run_git("merge-base", "--is-ancestor", branch, main, cwd=cwd).returncode == 0:
        return {"branch": branch, "state": "merged_by_ancestry", "safe_to_delete": True}
    unapplied = [
        line for line in git("cherry", main, branch, cwd=cwd).splitlines() if line.startswith("+")
    ]
    if not unapplied:
        return {"branch": branch, "state": "merged_by_patch", "safe_to_delete": True}
    subject = normalize_subject(git("log", "-1", "--format=%s", branch, cwd=cwd))
    if subject and subject in subjects:
        return {
            "branch": branch,
            "state": "merged_by_subject",
            "safe_to_delete": True,
            "subject": subject,
        }
    return {
        "branch": branch,
        "state": "unresolved",
        "safe_to_delete": False,
        "unapplied_commits": len(unapplied),
        "subject": subject,
    }


def resolve(main="main", cwd=None):
    """Classify every local branch and summarize the backlog."""
    subjects = {normalize_subject(item) for item in main_subjects(main, cwd=cwd)}
    branches = [
        classify_branch(name, main, subjects, cwd=cwd) for name in local_branches(main, cwd=cwd)
    ]
    counts = {}
    for item in branches:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    return {
        "main_branch": main,
        "branches_examined": len(branches),
        "state_counts": counts,
        "safe_to_delete": sorted(item["branch"] for item in branches if item["safe_to_delete"]),
        "unresolved": sorted(item["branch"] for item in branches if not item["safe_to_delete"]),
        "branches": sorted(branches, key=lambda item: item["branch"]),
        "deletion_permitted_by": "BRANCHING.md; this report never deletes a branch",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--main-branch",
        default="main",
        help="Branch that merged work lands on. Each other branch is classified against it.",
    )
    parser.add_argument("--out", default=None)
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        report = resolve(args.main_branch)
    except (OSError, ValueError) as exc:
        sys.exit(f"Branch state failed: {exc}")
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(rendered)
    if not args.quiet:
        print(rendered, end="")


if __name__ == "__main__":
    main()

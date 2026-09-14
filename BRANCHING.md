# Branch Taxonomy and Change Memory

This file is the durable repository memory for change classification. Every
agent reads it through `AGENTS.md`; do not rely on chat history to preserve a
branching decision.

## Branches

- `main` is always releasable. Do not begin work directly on it. Merge only a
  reviewed, tested change that passes the full repository quality gate.
- `feature/<concise-description>` is for a new user-visible capability or a
  material extension of an existing workflow.
- `bug/<concise-issue>` is for a reproducible defect. Add or update a regression
  test before merging the fix.
- `security/<concise-issue>` is for a confidentiality, integrity, credential,
  authorization, dependency, or vulnerability correction. Keep secrets and
  client data out of commits and follow `SECURITY.md`.
- `docs/<concise-description>` is for documentation, runbook, agent-instruction,
  handoff, sample, or rendered-document changes that do not change runtime
  behavior.
- `release/vMAJOR.MINOR.PATCH` is a short-lived release-preparation branch.
  Create it from current `main` only when a stable version, changelog entry,
  and release notes need review together. Merge it through a pull request; the
  final release tag is created only from the resulting `main` tip.

Use lowercase kebab-case after the slash. Keep a branch until its merged commit
is safely on `main` and any requested remote retention has been confirmed. Do
not delete branches merely to reduce the branch list.

### Do not stack a branch on one that has not merged

`main` requires linear history and pull requests are squash merged, so a merged
branch's commits never appear on `main` -- a new commit replaces them. A branch
started from an unmerged branch therefore carries a parent that `main` will
never contain, and once the parent merges the child conflicts with `main` on
every file both touched. GitHub reports it `CONFLICTING`, and the required
checks may never run at all, so the pull request sits looking merely slow.

That is not hypothetical: PR #131 was branched from #130 before #130 merged,
conflicted the moment it did, and never triggered CI. It had to be closed and
reopened as #132 from current `main`.

Start every branch from an up-to-date `origin/main`. When work genuinely depends
on an unmerged change, wait for it to merge and branch again from the result.

To move a commit onto current `main` without disturbing a working tree that
holds someone else's uncommitted work -- which is the usual reason this is
awkward -- use a separate worktree rather than switching branches in place:

```bash
git fetch origin
git format-patch -1 <commit> -o /tmp/patch
git worktree add /tmp/fix -b <new-branch> origin/main
git -C /tmp/fix am /tmp/patch/*.patch
```

Verify the result is the same tree you tested (`git diff <commit> HEAD` in the
worktree must be empty), run the gate there, push from there, and remove the
worktree with `git worktree remove` when the branch has merged. A `git checkout`
in the shared tree would refuse, or worse would sweep another operator's
uncommitted files into the branch.

### Determining merge state

`main` requires linear history, so a merged branch is squashed or rebased and is
**not** an ancestor of `main`. `git branch --merged main` therefore reports
merged work as unmerged and cannot be used to decide whether a branch is safe to
delete.

Merge `main` pull requests with **squash merge** so every `main` commit records
its PR number. Merge state is then answerable mechanically:

```bash
python scripts/branch_state.py --out branch_state.json
```

Each branch resolves to `merged_by_ancestry`, `merged_by_patch`,
`merged_by_subject`, or `unresolved`. The first three are safe to delete; an
`unresolved` branch holds work that is not on `main` and must be inspected, never
deleted on the strength of its name. The report never deletes anything.

## Change discipline

1. Start the branch from current `main`; retain the branch category in the
   commit/PR description.
2. Preserve evidence and never commit client data, credentials, generated
   provider raw responses, or transient run output.
3. Run the quality gate appropriate to the change. A bug fix includes its
   regression test; a feature includes its contract and safety tests; docs that
   alter tracked Markdown regenerate the corresponding LaTeX/PDF artifacts.
4. Merge bug/security fixes into `main` before dependent feature work. Merge or
   rebase the feature onto the repaired `main`, then rerun its gate.
5. Record material branch state in `HANDOFF.md` so a new agent can continue
   safely without prior conversation context.

## Stable release procedure

A branch is not a release. A stable release is the immutable annotated tag
`vMAJOR.MINOR.PATCH`, its matching GitHub Release, and the exact `main` commit
they name. Use semantic versioning: increment MAJOR for incompatible public
contract changes, MINOR for backward-compatible capabilities, and PATCH for
backward-compatible corrections or a documentation-only release.

1. Start `release/vMAJOR.MINOR.PATCH` from up-to-date `main`; do not develop
   unrelated features there. Update the project version, `CHANGELOG.md`,
   `RELEASE.md`, relevant operator documentation, and generated delivery
   artifacts together.
2. Run the complete quality and acceptance gates in `AGENTS.md`, plus
   `python scripts/release_check.py --tag vMAJOR.MINOR.PATCH`. Open a pull
   request and require the normal protected-branch CI checks; an independent
   human approval is optional for this single-operator repository.
3. Merge the reviewed release branch into `main`. Confirm local `main` and
   `origin/main` resolve to the same merged commit and that the required PR
   checks are green. Do not release from a topic branch, a stale clone, or a
   commit with uncommitted changes.
4. Create one annotated tag from that exact commit, push it, and leave it
   immutable:

   ```bash
   git switch main
   git pull --ff-only origin main
   git status --short
   git tag -a vMAJOR.MINOR.PATCH -m "Business Document Ingestion vMAJOR.MINOR.PATCH"
   git push origin vMAJOR.MINOR.PATCH
   ```

   Use a signed annotated tag when signing is configured. Never force-push,
   retarget, or reuse a stable tag.
5. The tag-triggered `release` workflow verifies that the tag is exactly the
   current `origin/main` tip, checks the tag/version/release-notes contract,
   reruns strict quality and acceptance controls, verifies generated documents,
   and creates the GitHub Release. Wait for it to pass before announcing the
   release.

If a release branch or tag check fails, fix the issue through a new reviewed
commit on `main` and release the next valid PATCH version. Do not move a failed
or published tag. A GitHub Release may be marked as a draft or removed only as
an administrative correction; its retained tag and audit trail remain intact.

## GitHub protection and agent handoff

`main` must require pull requests, up-to-date passing **Strict quality gate
(Python 3.12)** and **Acceptance controls (Python 3.12)** checks, resolved
review conversations, and linear history. This repository is maintained by a
single authorized operator, so the required approving-review count is zero;
the pull-request and CI gates remain mandatory. Disable direct pushes, force
pushes, and branch deletion; include administrators in the rule. The repository
owner configures these settings in GitHub branch protection or rulesets because
they cannot be made effective by a checked-in file alone.

Before any branch, merge, tag, push, or deletion operation, agents must read
this file and `RELEASE.md`. They must state the branch category, preserve
unrelated changes, record material continuation state in `HANDOFF.md`, and
never create a release tag without the completed release procedure above.

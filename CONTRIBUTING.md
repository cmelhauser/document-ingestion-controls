# Contributing

Courtesy credit for the project is recorded in `NOTICE` for Christopher
Melhauser and theonlymuffinbot.

The `theonlymuffinbot` credit refers to collaborative AI work produced through
a mix of Anthropic Claude Opus 5, OpenAI GPT-5.6 Terra and Sol, and xAI Grok
4.5 models. It is descriptive courtesy credit only; legal attribution remains
with human contributors.

The project is released to the public domain under The Unlicense. Contributions
and patches are welcome. By submitting a contribution, you confirm that you
have the right to submit it and agree that it may be included in the same
public-domain release.

Before making a change:

1. Read `SKILL.md`, `AGENTS.md`, and the relevant reference runbook.
2. Keep client data, credentials, provider responses, and generated run output
   out of version control.
3. Use the branch taxonomy in `BRANCHING.md`; keep `main` releasable.
4. Add regression or contract tests for behavior changes.
5. Run the complete quality gate in `AGENTS.md` and `scripts/release_check.py`.
6. Update affected documentation and regenerate tracked documentation artifacts
   when Markdown sources change.

For a stable release, also read `RELEASE.md`. Prepare it on a
`release/vMAJOR.MINOR.PATCH` branch, merge through a reviewed pull request, and
create the matching immutable annotated tag only from the exact `main` tip.
Do not move or reuse a stable tag; a failed release is corrected and published
as a new PATCH version.

Questions and authorization requests should be directed to Christopher
Melhauser at <christopher.melhauser@gmail.com> and theonlymuffinbot at
<theonlymuffinbot@outlook.com>.

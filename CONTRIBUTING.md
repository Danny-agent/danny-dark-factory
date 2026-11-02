# Contributing

Until Phase 1 is reviewed and explicitly enabled, this repository contains a queue/architecture prototype only. It does not execute supplied tasks or authorize agents to act.

## Local development

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

Contributions should include tests for task lifecycle and concurrency, update the threat model for new capabilities, and keep all externally sourced claims linked/cited in `docs/RESEARCH.md`.

## Security and review rules

- Never add credentials, private keys, access tokens, or production secrets to the repository, issues, fixtures, or logs.
- Do not grant worker tokens repository administration, secrets access, branch protection edits, merge, release, or deployment permissions.
- Any future worker runtime must be tested first in a disposable repository with adversarial tasks. Prompt instructions alone are not a security boundary.
- Changes to `.github/workflows/`, `docs/CHARTER.md`, verification logic, or sandbox policy need explicit human review.
- Every PR must identify its task, list acceptance criteria and test evidence, and stop at draft/review until a human merge.
- We measure accepted, correct work and defects/rework—not agent activity, lines, or PR count.

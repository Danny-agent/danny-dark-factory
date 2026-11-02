# Danny Dark Factory

Public, human-reviewed software-factory prototype. **This repository currently accepts task metadata only; it cannot run agents, open PRs, merge changes, or deploy.**

- [Charter](docs/CHARTER.md) — what the factory may eventually do and hard stops.
- [Architecture](docs/ARCHITECTURE.md) — component boundaries and intended workflow.
- [Threat model](docs/THREAT_MODEL.md) — assets, failure cases, and guardrails.
- [Research](docs/RESEARCH.md) — research evidence and component-selection rationale.
- [Roadmap](docs/ROADMAP.md) — staged adoption plan.
- [Contributing](CONTRIBUTING.md) — development and security rules.

## Quickstart

Requires Python 3.11+.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
factory init ./factory-state
factory task-add ./factory-state --title 'Example task' \\
  --description 'Describe one bounded, reversible unit of work.' \\
  --risk low --acceptance 'Named checks pass'
factory task-list ./factory-state
```

The ledger stores metadata only; it does not execute task instructions. Do not put secrets or production credentials in task descriptions.

## Current limits

The task ledger stores metadata only; it does not execute task instructions. `main` now requires a pull request, one code-owner approval, last-push approval, and resolved conversations; force-pushes and branch deletion are disabled. Required CI checks are not active yet: [the workflow template](docs/workflows/tests.yml.example) can be installed after the authorized GitHub workflow scope is approved. Do not grant any agent write access to `main`.

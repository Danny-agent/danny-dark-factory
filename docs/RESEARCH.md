# Dark-factory evidence and component selection

This is an implementation notebook, not a claim that the referenced projects have been security-audited or proven interoperable. Links were checked during the initial design/repo sweep; recheck upstream releases, license, hosting, and current docs before integrating.

## What the evidence says—and does not say

- A 2025 METR randomized study observed a 19% slowdown for experienced open-source developers using then-current tools in its selected tasks; METR explicitly labels the result a historical snapshot.[1] In its 2026 follow-up update, METR says its newer raw results point toward speedup but selection and measurement problems make the estimate unreliable; do not turn either study into a universal productivity guarantee.[19]
- DORA's 2025 survey of almost 5,000 technology professionals found positive relationships between AI adoption and throughput/product performance but a negative relationship with delivery stability. This is survey evidence about associations, not proof that AI caused those outcomes.[2]
- EPAM reports an internal frontend workflow that generated a feature in about two hours versus about two developer-days historically, then required another 2–4 hours of human review/refinement; the authors report reliability took weeks to improve and repeatability remained a limitation.[3] This is one company's case report, not a controlled trial.
- A secondary case-study write-up describes Twin Sun's incremental adoption of a reviewer, then coding and verification workflows. It reports early high approval rates but also acknowledges substantial upfront investment, limits in visual evaluation, capped correction cycles, and continued human approval in some settings.[4] Treat the reported approval percentage as a vendor-reported operational metric, not an independently measured correctness rate.
- Security benchmarks are a separate warning from productivity metrics: SusVibes reports weak security performance for tested coding-agent setups on its constructed real-world security-sensitive task set.[11] A prompt-injection study reports that poisoned development resources can induce tool-using coding agents to issue malicious commands in its evaluated settings.[12] These do not prove every agent/runtime is exploitable; they justify testing capability boundaries rather than trusting prompt language.
- CaMeL proposes a design-oriented approach using separation of data and control flow to resist prompt injection, while discussing its own utility trade-offs and unresolved attack surfaces.[13] The practical lesson is to enforce policy outside the model and shrink capabilities; do not consider this report or one sandbox an absolute security guarantee.
- A 2025 difference-in-differences study of Cursor-adopting GitHub projects reports short-lived increases in some output measures alongside persistent increases in static-analysis warnings and code complexity; this project-level observational result is not a universal productivity estimate.[14]
- Self-reported productivity surveys are not controlled output measurements. METR's 2026 survey reports respondents' estimates but notes reasons to doubt their magnitude.[20] Our pilot must measure accepted work and defect/rework outcomes, not token count, lines added, or PRs opened.

## What we borrow / what we will not copy blindly

### Repository-owned factory method (`addyosmani/factory`)

Its README uses GitHub Issues as the queue, keeps charter/contracts/gates in the target repo, makes runs bounded and restartable, asks a fresh verifier to inspect implementation, uses draft PR handoff, and reserves merge for a human.[6] This is a good *operating pattern* to adopt. We do not need to install all the Claude/Codex routine-specific scaffolding; the current local ledger first tests state transitions and race behavior.

### OpenAI Symphony

The project describes isolated implementation runs and is presented as a low-key engineering preview for trusted environments.[5] Treat it as an adapter/runtime candidate for a disposable-repo evaluation, not a production dependency by default. Check issue tracker support, license, versions, auth boundary, and compatibility with the actual runner before selection.

### Dark Factory by `dark-factory-build`

Its own README says it requires macOS, runs worker agents as the current user, and has a sandbox boundary tied to that host.[18] That makes it a poor deployment fit for Danny's Debian server without a port or a separate supported runtime. It is useful as a reference for per-task worktrees, private runtime homes, budgets, and an operator console; it is not installed here.

### Other projects worth evaluating one at a time

- **OpenHands**: evaluate its actual runtime/sandbox configuration in a disposable repository; don't infer that any default container is a complete boundary.[7]
- **Temporal**: consider only if we prove we need durable long-running workflow recovery, leases, or multi-stage retries beyond the current ledger.[8]
- **Dagger**: potential reproducible, task-scoped CI/check execution layer, subject to a threat-model and integration test.[15]
- **LangGraph**: possible graph runtime, but do not add it alongside Temporal until we know which component owns durable state and retries.[16]
- **Fabro**: promising graph-oriented workflow and sandbox candidate; verify runtime assumptions and any cloud-sandbox dependency before adopting.[17]
- **OpenSymphony / symphony-ts**: community implementations of the Symphony approach; evaluate maturity and security surface, not star counts.[22][23]

The rule: one canonical queue, one workflow authority, one retry/lease mechanism, and one isolation boundary. Pluggable interfaces are useful; duplicating the same responsibility across multiple frameworks is not.

## Jev integration design

Jev is documented as a typed decision model: it accepts text state and questions, returns structured decisions/probabilities, and does not generate code or explanations.[9] Its confidence is calibrated across groups, not a promise that a particular answer is right.[9] Current model docs describe the hosted API and versioned models/pricing; no self-hosted Jev deployment path was identified in the docs reviewed, so verify residency, retention, availability, terms, and cost before sending repository or issue content.[10]

Potential use, if later approved:

1. Let deterministic checks classify objectively verifiable facts: test exit, changed protected paths, requested acceptance evidence.
2. Use Jev only for a narrow typed decision, such as `low-risk / needs-spec / escalate`, first in shadow mode on labeled tasks.
3. Compare decisions with human labels; track false-accept and false-reject rates by task class and check calibration on held-out examples.
4. Allow abstention/escalation. A Jev result cannot authorize shell access, widen scope, suppress a failed gate, approve its own suggestion, merge, or deploy.
5. Keep full task text, patches, credentials, and proprietary context out of a hosted decision call until data handling is explicitly acceptable.

Jev is optional. A small deterministic policy may be enough at this stage; don't add a hosted model simply because it exists.

## Initial acceptance/evaluation plan

- Start with documentation and small reversible bug-fix tasks in a disposable branch.
- Record baseline human time and review effort for a matched task sample before claiming productivity gains.
- Score completion only when acceptance criteria and independent tests are satisfied at the exact commit; report blocked/incomplete separately.
- Repeat the same small task class to estimate run-to-run variance; do not infer stability from a single impressive demo.
- Record accepted/rejected rate, defects found at review, regressions/reverts, reviewer minutes, time-to-accepted-change, CI flake rate, cost/task, prompt injections blocked, and containment incidents.
- Retain human merge/deploy and require a stop threshold for review queue size, retries, wall-clock time, and cost.
- Run red-team tasks in a throwaway repository before any agent has write access to this private project.

## Sources

[1] https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study
[2] https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report
[3] https://www.epam.com/insights/ai/blogs/building-a-dark-factory-with-ai-agents
[4] https://www.zenml.io/llmops-database/building-an-ai-powered-software-factory-with-autonomous-code-generation-and-review
[5] https://github.com/openai/symphony
[6] https://github.com/addyosmani/factory
[7] https://github.com/All-Hands-AI/OpenHands
[8] https://docs.temporal.io/workflows
[9] https://docs.typesafe.ai/concepts/system-one
[10] https://docs.typesafe.ai/models
[11] https://arxiv.org/html/2512.03262
[12] https://arxiv.org/abs/2509.22040
[13] https://arxiv.org/pdf/2503.18813
[14] https://arxiv.org/html/2511.04427v2
[15] https://github.com/dagger/dagger
[16] https://github.com/langchain-ai/langgraph
[17] https://github.com/fabro-sh/fabro
[18] https://github.com/dark-factory-build/dark-factory
[19] https://metr.org/blog/2026-02-24-uplift-update
[20] https://metr.org/blog/2026-05-11-ai-usage-survey
[22] https://github.com/kumanday/OpenSymphony
[23] https://github.com/sociotechnica-org/symphony-ts

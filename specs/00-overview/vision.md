# Vision

## Problem

Picking an LLM today is guesswork. Public leaderboards measure someone else's tasks, on someone else's settings, and hide what actually matters when you depend on a model: does it answer the **same way twice**, how long and how much does it take, does it **follow the instructions to the letter**, and can it solve problems that are **genuinely hard** for current models? Every provider exposes a different set of knobs (reasoning effort, thinking budget, verbosity, vision, tools...), so a "fair" comparison needs each model tuned to what it supports. And when a model writes a program or an HTML page, reading it is not enough: you have to **run** it — without letting untrusted generated code touch the machine running the benchmark.

## Solution

**AIDriven** is a local Python application with a web UI that works as a **test bench for LLMs**:

- **Models fully configurable from the UI**: provider connections and model profiles with every parameter the model supports (effort, thinking budget/level, verbosity, temperature, seed, vision, tools, JSON mode, pricing). The UI only offers what the model's `Capabilities` allow, and the runner validates before sending.
- **Configurable, unlimited battery of tasks**: a seed battery of very hard, verifiable tasks (interpreters, regex engines, logic puzzles, optimization, SQL, HTML apps, strict-format and spec-conformity, vision) plus any task the user adds, imports or exports.
- **Rules and agents** that apply to a battery: rulesets injected into the system prompt and agents that solve tasks with sandboxed tools.
- **Everything is captured**: input/output/reasoning/cached tokens, latency, time to first token, tokens/s, cost, retries, refusals, errors, transcripts and artifacts.
- **Score 1–10 with a human in the loop**: automatic checks give an objective `auto` score, a configurable **judge model** gives a **preview**, and the **user** has the final word.
- **Artifacts reproduced in isolation**: generated programs and HTML pages run inside a Docker sandbox (network off, limits, read-only root) or, if Docker is not available, a restricted subprocess with a visible warning.
- **Comparable results**: per task (A vs B with deltas) and overall (radar of the four dimensions, bars, tables), between runs, models and re-runs.
- **Persistence local first, Firestore when configured**: SQLite out of the box; Firestore (collections prefixed `aidriven_`) once the service-account JSON is uploaded from the UI. Artifacts stored locally or in Firebase Storage.

**The measurable outcome is four dimensions per model**: **reliability**, **performance**, **conformity** and **intelligence**, each normalized to 0–10 (see [../03-modules/evaluation.md](../03-modules/evaluation.md)).

## Target use cases

1. **Choosing a model for a project**: run the same battery against three candidate profiles and compare the radar and the per-task deltas.
2. **Tuning a single model**: compare `reasoning_effort=low` vs `high` (two profiles of the same model) on cost, latency and score.
3. **Measuring stability**: run a battery with 5 repetitions and read pass@k and the standard deviation per task.
4. **Regression after a model update**: re-run an old run with its original snapshot and compare original vs re-run.
5. **Evaluating rules and agents**: the same suite with and without a ruleset, or single-shot vs agent with sandbox tools.
6. **Building a private benchmark**: add company-specific tasks with hidden tests and keep them versioned.

## What it is NOT

- Not a public leaderboard or a hosted SaaS: it is a local, single-user tool (`127.0.0.1`).
- Not a jailbreak or red-teaming tool: seed tasks are hard **technically**, not borderline; tasks that trigger refusals are a defect of the task (see [../03-modules/tasks.md](../03-modules/tasks.md)).
- Not an automatic truth machine: the judge is a preview; the user's score prevails ([P7](constitution.md)).
- Not a general code-execution platform: the sandbox only runs artifacts and checks produced during a run.
- Not a training or fine-tuning tool.

## Success criteria

- With two model profiles configured and the seed battery, a run completes end to end and shows the four dimensions per model with every metric captured.
- A generated HTML artifact can be viewed in the UI and a generated Python program re-executed in the sandbox without touching the host.
- The user can review judge previews and override scores; the overall comparison updates instantly.
- Re-running a run (full, some tasks or selected results) produces a new run linked to the original and a comparison original vs re-run.
- Switching from SQLite to Firestore is done from the UI (upload JSON → test → activate → migrate) without touching code.
- Adding a new provider means adding one adapter; the domain and the UI pages do not change.

Related documents: [constitution.md](constitution.md) · [glossary.md](glossary.md) · [../01-architecture/architecture.md](../01-architecture/architecture.md)

# Prompts

Prompts are application logic. They live as Python constants next to the code that uses them, and this spec reproduces their normative content. Changing a prompt = a change to this spec in the same commit. Prompts are always in **English** regardless of UI language ([ADR-018](../01-architecture/adr/ADR-018-ui-i18n-as-built.md)); task content keeps its own language. Prompt ids are named in code comments; no prompt version is recorded in results yet.

## `runner.artifact_instructions` (`services/prompting.py::ARTIFACT_INSTRUCTIONS`)

Appended to the **user message** when the task declares `artifact_names`:

```
---
Delivery format (mandatory): return every requested file in its own fenced code block whose opening line is
exactly ```<language> file=<name> — for example ```python file=solver.py. Requested files: {names}.
Each block must contain the complete file content.
```

It never mentions hidden tests, rubric or reference answer. Answer tasks state the `FINAL ANSWER:` convention in their own prompt.

User message layout (`compose_user_message`): `<document name="…">…</document>` blocks for text attachments, then the task prompt, then the delivery instructions; images as image parts when the profile has vision.

## System prompt (`compose_system`)

`prepend` rulesets → agent persona → profile system prompt → `append` rulesets, joined with blank lines ([../03-modules/rules-agents.md](../03-modules/rules-agents.md)). There is no additional app-level system text.

## `judge.score` (`services/judge.py`)

Used by `JudgeService` ([../03-modules/evaluation.md](../03-modules/evaluation.md)). Non-streaming, the judge profile's params with `max_output_tokens = min(max(params, 4096), judge capabilities.max_output_tokens)`.

System (`JUDGE_SYSTEM`):

> You are a rigorous, impartial expert evaluator of AI-generated answers. You grade strictly against the task, the rubric and the reference. You never reward length, confidence or style over correctness. Reply with a single JSON object and nothing else.

User (`JUDGE_TEMPLATE`):

```
Grade the candidate answer to the task below on a 1-10 scale.

Scale: 10 = fully correct, complete and meets every requirement; 7-9 = correct with minor issues;
4-6 = partially correct or significant omissions; 2-3 = mostly wrong; 1 = wrong, empty or refused.

<task>{prompt}</task>
<rubric>{rubric | "Correctness, completeness and adherence to every instruction."}</rubric>
<reference>{reference_answer | "(none provided)"}</reference>
<automated_checks>{one line per check: "- [dimension] name: PASS|FAIL (xx%)|SKIPPED — first detail line" | "(no automated checks)"}</automated_checks>
<candidate_answer>{answer ≤ 60,000 chars + "…[truncated]" | "(empty answer)"}</candidate_answer>

Automated checks are reliable evidence of functional correctness: do not contradict a failed hidden test
unless the check itself is clearly broken. Return JSON exactly in this shape:
{"score": <number 1-10>, "criteria": [{"name": "<criterion>", "score": <1-10>, "comment": "<short>"}],
"rationale": "<2-4 sentences>"}
```

(In the code each tag and its content sit on separate lines, separated by blank lines.) The template contains no model name, provider or profile (blind judging, [P7](../00-overview/constitution.md)). The reply is parsed leniently (fenced JSON or outermost braces); `score` is clamped to [1, 10].

## Agent loop

There is no separate agent system prompt: the agent's `persona` is inserted in the system prompt and the tools describe the environment through their descriptions (`services/agent.py::TOOL_SPECS`):

- `run_python {code}` — "Run a Python 3 script (standard library only, no network) in an isolated sandbox. Files previously saved with write_file are available in the working directory. Returns exit code, stdout and stderr."
- `write_file {name, content}` — "Save a file in the working directory (it becomes a deliverable artifact). Overwrites."
- `run_html {name}` — "Open a saved HTML file in a headless browser (no network) and return console errors and the visible text of the page after load."
- `read_attachment {name}` — "Read a text attachment of the task by name."

All schemas set `additionalProperties: false`.

## Other fixed prompts

- Connection test (`BaseAdapter.test_connection`): user message "Reply with the single word: pong".
- HTML probe (`services/agent.py::HTML_PROBE`, also used by *Render headless*): opens the page with `open_page`, waits 500 ms and prints `{"errors": [...], "text": <visible text ≤ 4000>}`.

## Evaluation of prompts

Not automated yet. A golden set (structure, no leakage of model name/rubric to evaluated models, judge ordering on bad/partial/correct solutions) is a follow-up ([../08-roadmap/phases.md](../08-roadmap/phases.md#next)).

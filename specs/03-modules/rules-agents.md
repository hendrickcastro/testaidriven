# Module: Rules and agents

Entities `Ruleset` and `AgentDef`, `services/prompting.py` (system prompt), `services/agent.py` (`AgentRunner`) and the *Rules and agents* page (`/rules`). Rules and agents are configured once and **applied to a battery** ([battery.md](battery.md)), so the same tasks can be measured with and without them.

## Rulesets

Markdown instructions injected into the system prompt of every item of a run that selects them. Fields: name, description, content, `position` (`prepend` | `append`).

System prompt assembly (`compose_system`), parts joined with a blank line, empty parts dropped:

```
[content of rulesets with position=prepend, in suite order]
[agent persona, if the run has an agent]
ModelProfile.system_prompt
[content of rulesets with position=append, in suite order]
```

Ruleset content is inserted as-is (no heading wrapper). The delivery instructions for artifacts go into the **user** message, not the system prompt ([../05-llm/prompts.md](../05-llm/prompts.md)). The assembled system prompt is not stored in the result.

The bootstrap creates two rulesets: **Rigor** (`rules_rigor`: verify against every requirement, state assumptions) and **Concise output** (`rules_concise`: no filler), both `append`.

## Agents

An `AgentDef` (persona, `tools` subset, `max_turns` default 8, `tool_timeout_s` default 30) turns a single-shot item into an **agentic loop** when the evaluated profile has `capabilities.tools`:

| Tool | Arguments (JSON Schema) | Effect (always in the `SandboxPort`) |
|---|---|---|
| `write_file` | `name`, `content` | Saves a file in the agent workspace (in memory); it becomes an **artifact** of the result. Returns `saved <name> (<n> chars)` |
| `run_python` | `code` | Runs the code as `_main.py` with every workspace file in a fresh sandbox job (`timeout = tool_timeout_s`); returns JSON `{exit_code, timed_out, stdout (last 8,000 chars), stderr (last 3,000)}` |
| `run_html` | `name` (default `index.html`) | Opens the workspace HTML file with the harness' `open_page` in the Playwright image; waits 500 ms; returns the same JSON whose stdout holds `{errors[≤20], text (visible text ≤ 4,000)}` |
| `read_attachment` | `name` | Returns a text attachment of the task (≤ 8,000 chars); for images, a note that the image was provided in the first message; unknown names list the available ones |

Loop (`AgentRunner.run`):

1. The request gets the selected tool specs; each turn calls `adapter.generate` with the full message history.
2. The assistant turn is recorded in the transcript `{turn, role: "assistant", text, tool_calls}`. If there are no tool calls, or the response is a refusal, the loop ends.
3. Otherwise the assistant message is appended **with its provider-native content** (`provider_raw`: Anthropic content blocks incl. thinking signatures, OpenAI Responses output items incl. encrypted reasoning, Gemini `Content`) so reasoning state is replayed exactly; each tool call is executed (errors become `tool error: …` text), recorded `{turn, role: "tool", name, output[≤2,000]}` and appended as a tool message.
4. After `max_turns` turns without a final answer, `"\n\n[agent stopped: max_turns reached]"` is appended to the final text; the status stays `done`. The item timeout (`task_timeout_s`) wraps the whole loop (`timeout` status).
5. Final text = the last non-empty assistant text. Metrics accumulate over turns ([execution.md](execution.md)): `turns` = model calls, `tool_calls` = executed calls.
6. Artifacts = `write_file` files plus fenced files found in any turn's text; on a name clash the `write_file` version wins.

The bootstrap creates **Python engineer (sandbox)** (`agent_python_engineer`): all four tools, `max_turns=10`.

## Business rules

- A profile **without** `tools` runs the item single-shot even if the run has an agent (the persona is still part of the system prompt); the page hint states it: "Models without tool support run the task without the agent loop."
- The workspace is private to one item and only lives in memory; every tool call runs in a fresh sandbox job.
- Tool outputs are passed back as tool results, never merged into the system prompt.
- Tool names and schemas are fixed by the app (`TOOL_SPECS`); an `AgentDef` selects a subset.
- Editing a ruleset or agent from the UI increments `version`; runs keep their snapshot copy.
- Deleting a ruleset or agent asks for confirmation; suites that referenced it drop it silently.

## Acceptance criteria

- [x] An agent with `run_python` on the fake `tools` model executes the tool in the sandbox and records `turns=2`, `tool_calls=1` and the tool output in the answer (`test_runner.py::test_agent_loop_with_tools`).
- [x] Anthropic tool loops replay the raw assistant content and group consecutive tool results in one user message (`test_adapters.py::test_anthropic_groups_tool_results_and_replays_raw_content`).
- [ ] A run with a `prepend` and an `append` ruleset produces the system prompt in the documented order.
- [ ] An agent run on a model without tools runs single-shot and still produces a result.
- [ ] Reaching `max_turns` ends the loop and keeps the partial transcript and artifacts.

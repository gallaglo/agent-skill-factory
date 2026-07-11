# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Skill Factory: a self-extending agent on Google's ADK (`google-adk` 2.4.0, Python ≥3.13, uv-managed). It solves ad hoc data-wrangling requests, and when a solved task looks like a repeatable pattern it drafts an Agent Skill (agentskills.io format), trial-runs it in a sandbox, and persists it to `skills/` **only after human approval**. The spec it implements is `docs/skill-factory-spec.md`; the README documents the user-facing behavior.

## Commands

```bash
uv sync                        # install deps into .venv (Python 3.14)
.venv/bin/adk web .            # browser UI; the app is "skill_factory"
.venv/bin/adk run skill_factory
```

- Config comes from `.env` (auto-loaded by adk; see `.env.example`). Model access is Vertex AI via ADC; `GOOGLE_CLOUD_LOCATION=global` is required — `us-central1` 404s for `gemini-3.5-flash`.
- The code executor must be configured or agent factories raise at first use. For local dev: `SKILL_FACTORY_EXECUTOR=unsafe-local`. Production path is `AgentEngineSandboxCodeExecutor` via `SKILL_FACTORY_SANDBOX_RESOURCE_NAME` / `SKILL_FACTORY_AGENT_ENGINE_RESOURCE_NAME` (selection logic in `skill_factory/executors.py`).

There is no unit-test suite or linter configured. Verification is an end-to-end drive of the workflow against the real model (~3–5 min, costs tokens), using temp dirs so the real `skills/` registry is untouched:

```bash
.venv/bin/python scripts/e2e_check.py            # grow → approve → persist → match
.venv/bin/python scripts/e2e_check.py --reject   # reject path (nothing persists)
```

`scripts/e2e_check.py` is also the reference for driving the app programmatically: `InMemoryRunner(app=...)`, and resuming the HITL interrupt by sending a `types.FunctionResponse(id=<interrupt fc id>, name="adk_request_input", response={"approved": ...})` as the user message. Any driver script MUST guard `if __name__ == "__main__":` — `UnsafeLocalCodeExecutor` uses multiprocessing spawn, which re-imports `__main__`.

Cheap smoke test (no model calls): `SKILL_FACTORY_EXECUTOR=unsafe-local .venv/bin/python -c "import skill_factory.agent"` — building the `Workflow` validates the whole graph.

## Architecture

The root agent (`skill_factory/agent.py`) is a `Workflow` (graph-based ADK Workflow Runtime) wrapped in a resumable `App` — resumability is what lets the human-approval interrupt pause one invocation and resume it on the next user message.

The graph (`skill_factory/workflow.py`):

```
intake ─┬─ use_skill → execute_skill ──────────────────────────→ respond
        └─ solve → reflect ─┬─ no_pattern ─────────────────────→ respond
                            ├─ draft_skill ─┐
                            └─ draft_mcp ───┴→ sandbox_test
                                 ↑ (revise_* loop, bounded)  │
                                 └───────────────────────────┤
                                              review → hitl_gate ⏸
                                                 ├─ reject ────→ respond
                                                 └─ approve → persist → respond
```

Two kinds of nodes, split across two files:

- `agents.py` — LLM agents. `intake`/`reflect` are single-turn structured-output agents and static graph nodes. `solve_task`, `skill_executor`, `skill_draft`, `mcp_draft` are **task-mode agents (ADK Task API)** created fresh per dispatch by `make_*()` factories and invoked dynamically from function nodes — task-mode agents *cannot* be static graph nodes. Draft agents get the vendored `seed_skills/` (`skill-creator`, `mcp-builder`) guidance inlined into their instructions.
- `nodes.py` — function nodes: routing (set `ctx.route`), dynamic dispatch (`ctx.run_node`), the sandbox trial, the HITL gate (yields `RequestInput`; the reviewer's response becomes the node's output), persist, and the single terminal `respond` node that composes the final answer from state.

Data flow between nodes: function-node parameters are bound **by name from `ctx.state`**; `node_input` receives the upstream node's output. The state key contract is documented (not enforced) by `schemas.FactoryState` — keep new state keys declared there.

The skill registry (`registry.py`) is disk-backed and re-read on every request: `skills/` is the durable long-term memory. It grows only through the approval flow — `persist` stages files in a temp dir, blocks path traversal, validates with ADK's own `load_skill_from_dir`, and refuses to overwrite. Never write to `skills/` directly. `seed_skills/` is read-only vendored content from anthropics/skills (note: its `reference/` dir was renamed `references/` for ADK compatibility).

All code execution (solving, skill scripts, sandbox trials) goes through the single process-wide executor from `executors.get_code_executor()` — never in-process. Models get it as the explicit `run_python_code` tool (`tools.py`).

## ADK 2.4.0 pitfalls (each of these caused a real bug)

- Always pass a **unique `override_isolation_scope`** to `ctx.run_node` when dispatching task agents (see `_dispatch_scope` in nodes.py). Without it the child sees the parent workflow's whole conversation — a redrafted agent "remembers" already finishing and returns `None` instead of calling `finish_task`.
- Function nodes that call `ctx.run_node` must be created with `rerun_on_resume=True` (ADK hard-requires it). The HITL gate is the exception: `rerun_on_resume=False` makes the resume payload become the node output.
- Do **not** set `state_schema` on the Workflow: ADK toolsets write internal `_adk_*` state keys that a strict schema rejects at runtime.
- Setting `code_executor=` on an LlmAgent does not tell the model it can run code; models then hallucinate tool calls (e.g. `run_command`), and an unknown tool call kills the node. Use the `run_python_code` FunctionTool instead.
- Graph cycles are only valid if the cycle contains a routed (conditional) edge — the `revise_skill`/`revise_mcp` loop edges satisfy this.
- Task-mode agents complete **only** via `finish_task`; a text reply ends the turn with no output. Draft instructions therefore insist on unattended operation, and `_run_draft` retries once on a `None` result.

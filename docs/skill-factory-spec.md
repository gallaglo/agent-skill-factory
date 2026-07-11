# Skill Factory — ADK Self-Extending Agent

## 1. Overview

A personal automation agent, built on Google's Agent Development Kit (ADK 2.x, Python),
that handles ad hoc data-wrangling / file-conversion requests and — when it notices a
repeated, generalizable pattern — writes itself a permanent Agent Skill to handle that
pattern in the future. New skills are drafted, test-run in an isolated sandbox, and
require explicit human approval before they persist and change the agent's future
behavior.

This is a local-first build: no production deployment is required to run or demo it.
Runs via `adk run` / `adk web`.

**Build environment:** Claude Code (CLI), using Claude Fable 5.

## 2. Goals

- Solve real one-off automation requests in the data-wrangling / file-conversion domain
  (CSV↔JSON, reformatting, batch renaming, extraction, small transform scripts, etc.).
- Detect when a solved task represents a reusable pattern, not a one-off.
- Draft new skills in the [agentskills.io](https://agentskills.io) spec format
  (`SKILL.md` + optional `references/`, `assets/`, `scripts/`), consistent with
  Anthropic's public `anthropics/skills` repo layout.
- Safely trial-run drafted skills before they can affect future behavior.
- Require human approval ("review it like a code review") before any new skill is
  persisted.
- Grow a durable, on-disk skill library that changes what the agent can do in every
  future session — the skill directory *is* the long-term memory.
- Support a secondary growth path: when the repeated need is "talk to an external
  service" rather than "follow a procedure," scaffold a small MCP server instead of a
  plain instruction skill.

## 3. Non-Goals (v1)

- No production deployment / Cloud Run / GKE / Agent Runtime hosting required for v1.
- No incident-response / RCA functionality (separate, unrelated idea, not in scope here).
- No `CloudRunSandboxCodeExecutor` — not yet available in the ADK library as of the
  latest release (`google-adk` 2.4.0, July 7 2026); use the existing Agent Runtime Code
  Execution tool instead. Revisit once `CloudRunSandboxCodeExecutor` ships.
- No multi-user / team skill sharing in v1 — single local skill registry.
- No automated skill execution of `scripts/` via ADK's native `SkillToolset` loader —
  that's a known current limitation (experimental feature). Any script execution goes
  through the sandboxed code executor tool explicitly, not through automatic
  `scripts/` loading.

## 4. Core ADK Features Used

| Feature | Role in this app |
|---|---|
| **Workflow Runtime** (graph-based execution engine) | Orchestrates the end-to-end flow: intake → route → solve/skill-match → reflect → draft → sandbox test → HITL → persist. Uses conditional routing, a loop node for reflection, and dynamic nodes. |
| **Task API** | Delegates ad hoc problem-solving to a specialist Task agent, separate from the orchestrator managing the meta-loop. |
| **SkillToolset** | Progressive disclosure (`list_skills` / `load_skill` / `load_skill_resource`) over the growing skill registry. Seeded from `anthropics/skills` (`skill-creator`, `mcp-builder`) plus a local `skills/` directory. |
| **Human-in-the-Loop (`ToolConfirmation`)** | Mandatory approval gate before any generated `SKILL.md` is written to the permanent registry. |
| **Agent Runtime Code Execution tool** | Sandboxed execution for (a) the Solve Task agent running LLM-generated code against real files, and (b) the Sandbox Test node trial-running a drafted skill's example scenario before human review. |
| **Session/Memory services** | `InMemorySessionService` for v1 (local dev). The persistent skill directory itself is the durable long-term memory, independent of session service choice. |

## 5. Workflow Graph

```
[Intake Node]
  - classify the incoming request
      ↓
⟨Skill Match? — conditional node⟩
  - query registry (list_skills) for an existing match
      │
      ├── MATCH ─────────────► [Load & Execute Skill] ──► respond
      │
      └── NO MATCH
              ↓
        [Solve Task Agent]  (Task API, delegated)
          - solves the request ad hoc
          - uses Agent Runtime Code Execution tool for any code execution
              ↓
        [Reflect Node]  (loop-capable)
          - "does this represent a generalizable, repeatable pattern?"
              │
              ├── NO  ──────────────────────────► respond, discard
              │
              └── YES
                    ↓
              ⟨Pattern type?⟩
                    │
                    ├── "missing know-how" ──► [Skill Draft Agent]
                    │                            - instructed via the
                    │                              `skill-creator` skill
                    │                            - drafts SKILL.md +
                    │                              optional references/scripts
                    │
                    └── "missing tool/API access" ──► [MCP Draft Agent]
                                                         - instructed via the
                                                           `mcp-builder` skill
                                                         - scaffolds a small
                                                           MCP server instead
                    ↓ (either branch)
              [Sandbox Test Node]
                - runs the draft's example scenario via the Agent Runtime
                  Code Execution tool
                - captures execution output for review
                    ↓
              [HITL Approval Gate]  ⏸ (ToolConfirmation)
                - presents: draft diff + sandbox execution output
                    │
                    ├── REJECT ──► discard, respond with explanation
                    │
                    └── APPROVE
                          ↓
                    [Persist Node]
                      - writes SKILL.md (or MCP server scaffold) to
                        local skills/ (or mcp-servers/) directory
                      - registers via load_skill_from_dir so it's
                        available starting next request/session
                          ↓
                        respond — capability now permanent
```

## 6. Skill Registry (Seed State)

Loaded at startup via `load_skill_from_dir`, sourced from the public
`anthropics/skills` repo (same `agentskills.io` spec ADK implements):

- `skill-creator` — used by the Skill Draft Agent to author new, spec-compliant
  `SKILL.md` files.
- `mcp-builder` — used by the MCP Draft Agent to scaffold new MCP servers when the gap
  is tool access rather than procedural know-how.

Local, user-grown skills live in a separate `skills/` directory that starts empty and
grows only through the approval flow above — never written to directly.

## 7. Safety Requirements

- **No skill is ever persisted without explicit human approval.** This is
  non-negotiable — treat every generated `SKILL.md` like a dependency / code review,
  since a meta-skill's output becomes the agent's future behavior.
- **All code execution — ad hoc solving and sandbox testing alike — goes through the
  Agent Runtime Code Execution tool**, never executed directly in the orchestrator
  process.
- The HITL review surface must show both the generated artifact (diff) and the sandbox
  execution output together, not the artifact alone.
- Rejected drafts are discarded, not retried automatically — a rejection should not
  silently loop back into another draft attempt without new input.

## 8. Tech Stack

- **Language:** Python 3.10+
- **Framework:** `google-adk` (latest — currently 2.4.0), installed via
  `pip install google-adk`
- **Code execution:** Agent Runtime Code Execution tool (Vertex AI Agent Runtime)
  - Requires: Google Cloud project with Agent Platform API enabled
  - Requires: service account with `roles/aiplatform.user`
  - Requires: a provisioned sandbox environment (via Agent Runtime API), referenced by
    `sandbox_resource_name` or `agent_engine_resource_name`
  - Note: the agent itself does **not** need to be deployed to Agent Runtime to use
    this tool — it can run locally.
- **Model:** Gemini (default per ADK samples), configurable
- **Skill source:** local clone of `anthropics/skills` for seed skills, plus local
  `skills/` directory for generated ones
- **Local dev/run:** `adk run path/to/agent` or `adk web path/to/agents_dir`

## 9. Open Questions / Future Work

- Swap the Sandbox Test node's executor to `CloudRunSandboxCodeExecutor` once it ships
  in a future `google-adk` release (announced but not yet available as of the latest
  release checked).
- Consider routing drafted skills through ADK's evaluation framework (`EvalSet` /
  `EvalCase`, rubric-based evaluation) before the HITL gate, so reviewers see a
  pass/fail trajectory alongside the raw draft.
- Consider `FirestoreSessionService` or persistent observability (MLflow/OpenTelemetry)
  if this grows beyond a single-user local tool.
- Decide whether to widen scope beyond the data-wrangling/file-conversion domain once
  the v1 loop is validated.

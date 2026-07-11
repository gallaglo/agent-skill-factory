# Skill Factory — ADK Self-Extending Agent

A personal automation agent, built on Google's Agent Development Kit
(`google-adk` 2.4.0, Python), for ad hoc data-wrangling / file-conversion
requests. When it notices that a solved task is a repeatable, generalizable
pattern, it writes itself a permanent Agent Skill ([agentskills.io](https://agentskills.io)
format): the draft is trial-run in a sandbox and **requires explicit human
approval** before it persists and changes the agent's future behavior.

Local-first: runs via `adk run` / `adk web`, no deployment required.

## How it works

```
START → intake (classify + match against skill registry)
   ├─ match    → execute the existing skill ─────────────────────→ respond
   └─ no match → Solve Task agent (Task API + sandboxed code exec)
                  → reflect: reusable pattern?
                     ├─ no  ─────────────────────────────────────→ respond
                     ├─ "missing know-how"    → Skill Draft agent
                     │                          (instructed via skill-creator)
                     └─ "missing tool access" → MCP Draft agent
                                                (instructed via mcp-builder)
                  → sandbox test (trial-runs the draft's example scenario;
                     failures loop back into redrafting, bounded)
                  → HITL approval gate ⏸ (draft + sandbox output together)
                     ├─ reject  → discard ───────────────────────→ respond
                     └─ approve → persist to skills/ (or mcp-servers/) ─→ respond
```

Implemented with the ADK Workflow Runtime (conditional routing, a bounded
redraft loop edge, dynamic task-agent dispatch via `ctx.run_node`), the Task
API (`mode='task'` specialists), `SkillToolset` for progressive skill
disclosure, workflow interrupts (`RequestInput`) for the approval gate, and a
`BaseCodeExecutor` for every piece of code execution.

## Layout

| Path | Purpose |
|---|---|
| `skill_factory/` | The agent (`adk run skill_factory`) |
| `seed_skills/` | Read-only meta-skills vendored from [anthropics/skills](https://github.com/anthropics/skills): `skill-creator`, `mcp-builder` |
| `skills/` | User-grown skill registry. Starts empty; grows **only** through the HITL-approved persist node. This directory is the agent's long-term memory. |
| `mcp-servers/` | Approved MCP server scaffolds |

## Setup

```bash
uv sync                      # installs google-adk into .venv
cp .env.example .env         # then edit: model access + code executor
```

Model access: either Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=1`,
`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`) with `gcloud auth
application-default login`, or a `GOOGLE_API_KEY`.

Code executor (all generated code runs through this, never in-process):

- **Agent Engine code-execution sandbox** (the spec's default; needs a GCP
  project with the Agent Platform API enabled and `roles/aiplatform.user`):
  set `SKILL_FACTORY_SANDBOX_RESOURCE_NAME` or
  `SKILL_FACTORY_AGENT_ENGINE_RESOURCE_NAME`, or `SKILL_FACTORY_EXECUTOR=agent-engine`
  to auto-create one. The agent itself still runs locally.
- **Local dev fallback**: `SKILL_FACTORY_EXECUTOR=unsafe-local` runs code in
  a subprocess on your machine with no isolation. Demo use only.

## Run

```bash
adk web .                    # browser UI (recommended: renders the approval gate)
adk run skill_factory        # terminal
```

Try: *"Convert this CSV to a JSON array of objects (data inline) — I do this
every week."* — solve, reflect, draft, sandbox test, then the run pauses at
the approval gate showing the drafted files and the sandbox trial output.
Approve, and `skills/<name>/` exists; the next matching request in any
session is routed through the persisted skill instead of being re-solved.

## Safety properties

- **No skill persists without explicit human approval.** The persist node is
  only reachable through the approval route and re-checks the verdict.
- The review surface always shows the drafted artifact **and** its sandbox
  execution output together.
- Rejected drafts are discarded, never silently retried.
- All code execution (ad hoc solving, skill scripts, sandbox trials) goes
  through the configured `BaseCodeExecutor`.
- Persisted files are staged, path-traversal-checked, validated by ADK's own
  skill loader, then moved into place; existing capabilities are never
  overwritten.

## Notes / future work (spec §9)

- Swap the sandbox-test executor to `CloudRunSandboxCodeExecutor` once it
  ships in a future `google-adk` release.
- Optionally run drafts through ADK's eval framework before the HITL gate.
- `InMemorySessionService` for v1; the skill directory itself is the durable
  memory, independent of session backend.

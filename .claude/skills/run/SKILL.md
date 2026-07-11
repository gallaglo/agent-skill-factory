---
name: run
description: Launch the Skill Factory app locally (adk web / adk run) to see it working.
---

# Run Skill Factory

Config loads from `.env` (copy `.env.example` if missing — needs model
access plus a code executor; `SKILL_FACTORY_EXECUTOR=unsafe-local` for dev).

```bash
.venv/bin/adk web . --port 8000     # browser UI — renders the HITL approval gate
.venv/bin/adk run skill_factory     # terminal REPL
```

The app name is `skill_factory` (verify via `curl localhost:8000/list-apps`).

Demo prompt that exercises the full growth loop: paste a small CSV and ask
for JSON conversion, mentioning it's a weekly recurring chore. The run will
pause at the approval gate showing draft files + sandbox output; approving
creates `skills/<name>/` which future sessions match automatically.

To drive it headlessly instead (interrupt/resume included), follow the
pattern in `scripts/e2e_check.py`.

---
name: verify
description: Verify Skill Factory changes by exercising the real workflow end-to-end. Use after changing skill_factory/ code to confirm the loop still works.
---

# Verify Skill Factory

1. Cheap structural check first (no model calls, ~2s). Building the Workflow
   validates the whole graph (edges, routes, node names, agent factories):

   ```bash
   SKILL_FACTORY_EXECUTOR=unsafe-local .venv/bin/python -c "
   import skill_factory.agent
   from skill_factory.agents import make_solve_agent, make_skill_executor_agent, make_skill_draft_agent, make_mcp_draft_agent
   [f() for f in (make_solve_agent, make_skill_executor_agent, make_skill_draft_agent, make_mcp_draft_agent)]
   print('graph + agents OK')"
   ```

2. Full end-to-end drive against the real model (~3–5 min, costs tokens;
   needs model access in `.env`). Uses temp dirs — never touches `skills/`:

   ```bash
   .venv/bin/python scripts/e2e_check.py            # grow → approve → persist → match
   .venv/bin/python scripts/e2e_check.py --reject   # HITL rejection → nothing persists
   ```

   Run the variant that covers the changed path; run both for changes to
   nodes.py routing, the HITL gate, or persist.

3. For UI-facing changes, also confirm the server boots and lists the app:

   ```bash
   (.venv/bin/adk web . --port 8799 &) && until curl -s -o /dev/null localhost:8799/; do sleep 1; done \
     && curl -s localhost:8799/list-apps && pkill -f "adk web . --port 8799"
   ```

Judgment calls: the LLM decides routing (skill match, reflect verdict), so
phase outcomes can vary — a "one-off" verdict on the grow request is a test
failure signal only if it repeats. Any new driver script must guard
`if __name__ == "__main__"` (multiprocessing spawn re-imports `__main__`).

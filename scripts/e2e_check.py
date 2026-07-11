"""End-to-end check of the Skill Factory loop, driven programmatically.

Exercises: solve -> reflect -> draft -> sandbox test -> HITL interrupt ->
approve -> persist, then a fresh session that should match the new skill.
Pass --reject to exercise the rejection path instead (nothing persists).

Uses temp dirs for skills/mcp-servers, so the real registry is untouched.
Makes real model calls (~3-5 min). Requires model access configured in the
environment or .env (see .env.example); the code executor defaults to
unsafe-local here.

Run:  .venv/bin/python scripts/e2e_check.py [--reject]
"""

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Environment must be set before importing skill_factory.
if (REPO_ROOT / ".env").exists():
    for line in (REPO_ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)
SCRATCH = tempfile.mkdtemp(prefix="skill-factory-e2e-")
os.environ.setdefault("SKILL_FACTORY_EXECUTOR", "unsafe-local")
os.environ["SKILL_FACTORY_SKILLS_DIR"] = os.path.join(SCRATCH, "skills")
os.environ["SKILL_FACTORY_MCP_SERVERS_DIR"] = os.path.join(SCRATCH, "mcp-servers")
os.makedirs(os.environ["SKILL_FACTORY_SKILLS_DIR"], exist_ok=True)

sys.path.insert(0, str(REPO_ROOT))

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from skill_factory.agent import app  # noqa: E402

REQ_GROW = """Every Monday I get an orders export as CSV with a header row
(columns: order_id, customer, amount_usd) and I need it converted to a JSON
array of objects, with amount_usd as a number. Here is this week's data:

order_id,customer,amount_usd
1001,Acme Corp,250.50
1002,Globex,99.99
1003,Initech,1200.00

Please convert it. This exact CSV-to-JSON conversion comes up every single
week, so it's a recurring chore for me."""

REQ_MATCH = """Here's this week's orders CSV, same drill as always - convert
it to a JSON array of objects (amount_usd numeric please):

order_id,customer,amount_usd
2001,Umbrella,10.00
2002,Hooli,88.25"""


def show(ev):
    bits = [f"[{ev.author}]"]
    if ev.content and ev.content.parts:
        for p in ev.content.parts:
            if p.text and not getattr(p, "thought", False):
                bits.append(f"text={p.text[:160]!r}")
            if p.function_call:
                bits.append(f"FC={p.function_call.name}")
            if p.function_response:
                bits.append(f"FR={p.function_response.name}")
    if ev.output is not None:
        bits.append(f"output={str(ev.output)[:160]!r}")
    print(" ".join(bits), flush=True)


async def send(runner, sid, content):
    events = []
    async for ev in runner.run_async(user_id="u", session_id=sid, new_message=content):
        events.append(ev)
        show(ev)
    return events


def find_interrupt(events):
    for ev in reversed(events):
        for fc in ev.get_function_calls():
            if fc.name == "adk_request_input":
                return fc
    return None


async def main(approve: bool):
    runner = InMemoryRunner(app=app)
    skills_dir = os.environ["SKILL_FACTORY_SKILLS_DIR"]
    print(f"### scratch dirs at {SCRATCH}")

    print("\n=== PHASE 1: grow path (should end in HITL interrupt) ===")
    s1 = await runner.session_service.create_session(app_name=app.name, user_id="u")
    evs = await send(
        runner, s1.id, types.Content(role="user", parts=[types.Part(text=REQ_GROW)])
    )
    fc = find_interrupt(evs)
    assert fc, "no HITL interrupt was raised (did reflect say one-off?)"
    print(f"\n### interrupt id: {fc.id}")

    verdict = {"approved": approve, "feedback": "LGTM" if approve else "Too narrow"}
    print(f"\n=== PHASE 2: respond to gate with {verdict} ===")
    await send(
        runner,
        s1.id,
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=fc.id, name=fc.name, response=verdict
                    )
                )
            ],
        ),
    )

    persisted = sorted(os.listdir(skills_dir))
    print(f"\n### skills dir now contains: {persisted}")
    if not approve:
        assert not persisted, "REJECTED draft was persisted!"
        print("### REJECT-PATH PASS")
        return
    assert persisted, "approved draft was not persisted!"

    print("\n=== PHASE 3: new session, expect skill match ===")
    s2 = await runner.session_service.create_session(app_name=app.name, user_id="u")
    evs3 = await send(
        runner, s2.id, types.Content(role="user", parts=[types.Part(text=REQ_MATCH)])
    )
    authors = {e.author for e in evs3}
    assert "skill_executor" in authors, f"skill was not matched; authors={authors}"
    print("### GROW+MATCH PASS")


if __name__ == "__main__":  # required: UnsafeLocalCodeExecutor uses mp spawn
    parser = argparse.ArgumentParser()
    parser.add_argument("--reject", action="store_true", help="test the reject path")
    args = parser.parse_args()
    asyncio.run(main(approve=not args.reject))

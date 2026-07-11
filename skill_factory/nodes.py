"""Function nodes for the Skill Factory workflow graph.

Conventions (ADK 2.x Workflow):
- Parameters other than `ctx` / `node_input` are bound from ctx.state, so
  every name used here is declared in schemas.FactoryState.
- ctx.route selects the outgoing conditional edge.
- Nodes that dispatch dynamic child nodes (ctx.run_node) must be created
  with rerun_on_resume=True.
- The HITL gate yields RequestInput and completes on resume with the
  reviewer's response as its output (rerun_on_resume=False).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

from google.adk.code_executors.code_execution_utils import CodeExecutionInput
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.skills import load_skill_from_dir
from google.adk.utils.content_utils import extract_text_from_content
from google.genai import types
from pydantic import BaseModel

from .agents import (
    make_mcp_draft_agent,
    make_skill_draft_agent,
    make_skill_executor_agent,
    make_solve_agent,
)
from .config import (
    MAX_DRAFT_ATTEMPTS,
    MCP_SERVERS_DIR,
    SKILLS_DIR,
)
from .executors import get_code_executor
from .registry import user_skill_registry
from .schemas import ApprovalDecision, Draft, IntakeResult, ReflectResult

logger = logging.getLogger(__name__)

_PASS_SENTINEL = "__SANDBOX_TEST_PASSED__"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def _dump(value: Any) -> Any:
    return value.model_dump() if isinstance(value, BaseModel) else value


# --------------------------------------------------------------------------
# Intake routing
# --------------------------------------------------------------------------


def route_after_intake(ctx: Context, node_input: IntakeResult) -> dict:
    """Stores the intake result and routes to skill execution or ad hoc solve."""
    user_content = ctx.get_invocation_context().user_content
    request = extract_text_from_content(user_content) if user_content else ""
    request = request or node_input.task_restatement

    ctx.state["request"] = request
    ctx.state["intake"] = _dump(node_input)

    matched = node_input.matched_skill
    # Guard against hallucinated matches: the skill must exist on disk.
    if matched and matched in user_skill_registry().list_frontmatters():
        ctx.route = "use_skill"
    else:
        matched = None
        ctx.route = "solve"

    return {
        "request": request,
        "task": node_input.task_restatement,
        "matched_skill": matched,
    }


# --------------------------------------------------------------------------
# Skill-match path: load & execute the existing skill
# --------------------------------------------------------------------------


def _dispatch_scope(label: str) -> str:
    """Unique isolation scope per dynamic dispatch.

    Task agents dispatched via ctx.run_node otherwise share the parent
    workflow's conversation view — a redrafted agent would see its previous
    attempt's transcript and 'remember' having already finished.
    """
    return f"{label}:{uuid.uuid4().hex[:8]}"


async def execute_skill(ctx: Context, node_input: dict) -> dict:
    """Delegates to a task agent that loads and follows the matched skill."""
    result = await ctx.run_node(
        make_skill_executor_agent(),
        node_input={
            "task": node_input.get("task"),
            "matched_skill": node_input.get("matched_skill"),
        },
        override_isolation_scope=_dispatch_scope("skill-exec"),
    )
    ctx.state["skill_answer"] = _dump(result)
    return _dump(result)


# --------------------------------------------------------------------------
# No-match path: ad hoc solve, then reflect
# --------------------------------------------------------------------------


async def solve(ctx: Context, node_input: dict) -> dict:
    """Delegates the request to the Solve Task agent (Task API)."""
    result = await ctx.run_node(
        make_solve_agent(),
        node_input={"task": node_input.get("task")},
        override_isolation_scope=_dispatch_scope("solve"),
    )
    solve_result = _dump(result)
    ctx.state["solve_result"] = solve_result
    return {"request": node_input.get("task"), "solved": solve_result}


def route_after_reflect(ctx: Context, node_input: ReflectResult) -> dict:
    """Stores the reflection verdict and routes to drafting or plain response."""
    ctx.state["reflect_result"] = _dump(node_input)

    name_ok = bool(node_input.proposed_name) and bool(
        _NAME_RE.match(node_input.proposed_name or "")
    )
    if not node_input.is_reusable_pattern or not name_ok:
        if node_input.is_reusable_pattern and not name_ok:
            logger.warning(
                "Reflect proposed invalid name %r; treating as no pattern.",
                node_input.proposed_name,
            )
        ctx.route = "no_pattern"
    elif node_input.pattern_type == "tool-access":
        ctx.route = "draft_mcp"
    else:
        ctx.route = "draft_skill"
    return _dump(node_input)


# --------------------------------------------------------------------------
# Draft nodes (Task API dispatch, instructed via the seed meta-skills)
# --------------------------------------------------------------------------


def _draft_brief(
    kind: str,
    request: Optional[str],
    solve_result: Optional[dict],
    reflect_result: Optional[dict],
    draft_feedback: Optional[str],
) -> dict:
    brief = {
        "kind": kind,
        "name": (reflect_result or {}).get("proposed_name"),
        "original_request": request,
        "how_it_was_solved": solve_result,
        "why_reusable": (reflect_result or {}).get("rationale"),
    }
    if draft_feedback:
        brief["previous_attempt_failed_sandbox_testing"] = draft_feedback
        brief["instruction"] = (
            "Your previous draft failed its sandbox trial run (details"
            " above). Produce a corrected draft."
        )
    return brief


async def _run_draft(ctx: Context, agent, kind: str, brief: dict) -> dict:
    result = await ctx.run_node(
        agent, node_input=brief, override_isolation_scope=_dispatch_scope("draft")
    )
    if result is None:
        # The task agent ended its turn without finish_task (e.g. it tried
        # to converse). Retry once with an explicit reminder.
        retry_brief = dict(brief)
        retry_brief["reminder"] = (
            "You previously ended your turn without calling finish_task."
            " Do not reply with text; produce the complete draft and call"
            " finish_task now."
        )
        result = await ctx.run_node(agent, node_input=retry_brief)
    if result is None:
        raise RuntimeError(
            f"The {kind} draft agent finished without producing a draft"
            " (finish_task was never called)."
        )
    draft = Draft.model_validate(_dump(result))
    draft.kind = kind  # the brief, not the model, decides the kind
    if brief.get("name"):
        draft.name = brief["name"]
    dumped = draft.model_dump()
    ctx.state["draft"] = dumped
    ctx.state["draft_attempts"] = int(ctx.state.get("draft_attempts") or 0) + 1
    return dumped


async def draft_skill(
    ctx: Context,
    request: Optional[str] = None,
    solve_result: Optional[dict] = None,
    reflect_result: Optional[dict] = None,
    draft_feedback: Optional[str] = None,
) -> dict:
    """Drafts a SKILL.md package via the skill-creator-instructed agent."""
    brief = _draft_brief("skill", request, solve_result, reflect_result, draft_feedback)
    return await _run_draft(ctx, make_skill_draft_agent(), "skill", brief)


async def draft_mcp(
    ctx: Context,
    request: Optional[str] = None,
    solve_result: Optional[dict] = None,
    reflect_result: Optional[dict] = None,
    draft_feedback: Optional[str] = None,
) -> dict:
    """Scaffolds an MCP server via the mcp-builder-instructed agent."""
    brief = _draft_brief(
        "mcp-server", request, solve_result, reflect_result, draft_feedback
    )
    return await _run_draft(ctx, make_mcp_draft_agent(), "mcp-server", brief)


# --------------------------------------------------------------------------
# Sandbox test node
# --------------------------------------------------------------------------


def _build_sandbox_code(draft: Draft) -> str:
    """Self-contained script: materialize draft files in a temp dir, run the
    draft's test_script there, and print the pass sentinel on success."""
    files = {f.path: f.content for f in draft.files}
    files["__sandbox_test__.py"] = draft.test_script
    return f"""\
import os, runpy, tempfile
_files = {files!r}
_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as td:
    for rel, content in _files.items():
        norm = os.path.normpath(rel)
        if norm.startswith('..') or os.path.isabs(norm):
            raise PermissionError('Path traversal blocked: ' + rel)
        full = os.path.join(td, norm)
        os.makedirs(os.path.dirname(full) or td, exist_ok=True)
        with open(full, 'w', encoding='utf-8') as fh:
            fh.write(content)
    os.chdir(td)
    try:
        runpy.run_path('__sandbox_test__.py', run_name='__main__')
    finally:
        os.chdir(_cwd)
print({_PASS_SENTINEL!r})
"""


def _validate_draft_structure(draft: Draft) -> list[str]:
    """Static checks a draft must pass before it is worth trial-running.

    Failures feed the redraft loop, so messages are written for the model.
    """
    problems: list[str] = []
    paths = {f.path for f in draft.files}
    if draft.kind == "skill":
        if "SKILL.md" not in paths:
            problems.append("The draft must contain a SKILL.md at its root.")
        else:
            content = next(f.content for f in draft.files if f.path == "SKILL.md")
            try:
                from google.adk.skills._utils import _parse_skill_md_content
                from google.adk.skills.models import Frontmatter

                parsed_fm, _ = _parse_skill_md_content(content)
                fm = Frontmatter.model_validate(parsed_fm)
                if fm.name != draft.name:
                    problems.append(
                        f"SKILL.md frontmatter name '{fm.name}' must equal"
                        f" the skill directory name '{draft.name}'."
                    )
            except Exception as e:
                problems.append(
                    "SKILL.md is not spec-compliant: it must start with YAML"
                    " frontmatter delimited by '---' lines containing 'name'"
                    f" and 'description'. Parser said: {e}"
                )
    else:
        if not any(p.endswith(".py") for p in paths):
            problems.append("An mcp-server draft must include a Python server file.")
    if not draft.test_script.strip():
        problems.append("test_script must not be empty.")
    return problems


async def sandbox_test(ctx: Context, draft: dict) -> dict:
    """Trial-runs the draft's example scenario via the code executor."""
    parsed = Draft.model_validate(draft)

    problems = _validate_draft_structure(parsed)
    if problems:
        sandbox_result = {
            "passed": False,
            "stdout": "",
            "stderr": "Draft failed structural validation:\n- "
            + "\n- ".join(problems),
            "scenario": parsed.example_scenario,
        }
        ctx.state["sandbox_result"] = sandbox_result
        return sandbox_result

    code = _build_sandbox_code(parsed)
    executor = get_code_executor()
    invocation_context = ctx.get_invocation_context()
    try:
        result = await asyncio.to_thread(
            executor.execute_code, invocation_context, CodeExecutionInput(code=code)
        )
        stdout, stderr = result.stdout or "", result.stderr or ""
    except Exception as e:  # executor infrastructure failure
        stdout, stderr = "", f"Sandbox executor error: {e}"

    passed = _PASS_SENTINEL in stdout
    stdout = stdout.replace(_PASS_SENTINEL, "").strip()
    sandbox_result = {
        "passed": passed,
        "stdout": stdout[-4000:],
        "stderr": (stderr or "").strip()[-4000:],
        "scenario": parsed.example_scenario,
    }
    ctx.state["sandbox_result"] = sandbox_result
    return sandbox_result


def route_after_sandbox(
    ctx: Context,
    node_input: dict,
    draft: Optional[dict] = None,
    draft_attempts: int = 0,
) -> dict:
    """Pass -> human review. Fail -> bounded redraft loop, then review anyway."""
    kind = (draft or {}).get("kind", "skill")
    if node_input.get("passed"):
        ctx.state["draft_feedback"] = None
        ctx.route = "review"
    elif draft_attempts < MAX_DRAFT_ATTEMPTS:
        ctx.state["draft_feedback"] = (
            f"stdout:\n{node_input.get('stdout', '')}\n\n"
            f"stderr:\n{node_input.get('stderr', '')}"
        )
        ctx.route = "revise_mcp" if kind == "mcp-server" else "revise_skill"
    else:
        # Out of attempts: let the human see the failing draft and decide.
        ctx.route = "review"
    return node_input


# --------------------------------------------------------------------------
# HITL approval gate
# --------------------------------------------------------------------------


def _format_review(draft: Draft, sandbox_result: dict) -> str:
    lines = [
        "## Skill Factory — approval required",
        "",
        f"The agent wants to permanently add the {draft.kind} `{draft.name}`.",
        "Review it like a code review: once approved it changes the agent's",
        "behavior in every future session. Reply with approved=true to",
        "persist, approved=false to discard.",
        "",
        "### Draft files",
    ]
    for f in draft.files:
        lang = "python" if f.path.endswith(".py") else ""
        lines += [f"\n#### `{draft.name}/{f.path}`", f"```{lang}", f.content, "```"]
    status = "PASSED" if sandbox_result.get("passed") else "FAILED"
    lines += [
        "",
        f"### Sandbox trial run — {status}",
        f"Scenario: {sandbox_result.get('scenario', '(none)')}",
        "",
        "```",
        f"stdout:\n{sandbox_result.get('stdout') or '(empty)'}",
        "",
        f"stderr:\n{sandbox_result.get('stderr') or '(empty)'}",
        "```",
    ]
    return "\n".join(lines)


def hitl_gate(ctx: Context, draft: dict, sandbox_result: dict):
    """Interrupts the workflow until the human approves or rejects the draft.

    Presents the generated artifact AND the sandbox execution output
    together (spec §7). The resume payload becomes this node's output.
    """
    parsed = Draft.model_validate(draft)
    yield RequestInput(
        interrupt_id=f"skill_factory_approval:{parsed.kind}:{parsed.name}",
        message=_format_review(parsed, sandbox_result),
        payload={"draft": draft, "sandbox_result": sandbox_result},
        response_schema=ApprovalDecision,
    )


def route_after_review(ctx: Context, node_input: Any) -> dict:
    """Parses the reviewer's decision (leniently) and routes persist/discard."""
    decision = _parse_decision(node_input)
    ctx.state["review"] = decision.model_dump()
    ctx.route = "approve" if decision.approved else "reject"
    return decision.model_dump()


def _parse_decision(raw: Any) -> ApprovalDecision:
    if isinstance(raw, ApprovalDecision):
        return raw
    if isinstance(raw, types.Content):
        raw = extract_text_from_content(raw)
    if isinstance(raw, dict):
        for key in ("response", "payload", "input", "value", "result"):
            if set(raw.keys()) == {key}:
                return _parse_decision(raw[key])
        try:
            return ApprovalDecision.model_validate(raw)
        except Exception:
            raw = str(raw)
    if isinstance(raw, bool):
        return ApprovalDecision(approved=raw)
    text = str(raw).strip().lower()
    approved = text in ("true", "yes", "y", "approve", "approved", "lgtm", "ok")
    if not approved and "approved" in text and "true" in text:
        approved = True
    return ApprovalDecision(approved=approved, feedback=str(raw)[:2000])


# --------------------------------------------------------------------------
# Persist node
# --------------------------------------------------------------------------


def persist(ctx: Context, draft: dict, review: dict) -> dict:
    """Writes the approved draft to the permanent registry.

    Only reachable through the 'approve' route; re-checks approval anyway.
    """
    if not review or not review.get("approved"):
        raise PermissionError("persist called without an approved review")

    parsed = Draft.model_validate(draft)
    if not _NAME_RE.match(parsed.name):
        raise ValueError(f"Unsafe draft name: {parsed.name!r}")

    base = SKILLS_DIR if parsed.kind == "skill" else MCP_SERVERS_DIR
    target = base / parsed.name
    if target.exists():
        raise FileExistsError(
            f"{target} already exists; refusing to overwrite an existing"
            " capability. Rename the draft and try again."
        )

    # Stage in a temp dir, validate, then move into place atomically.
    staging_root = Path(tempfile.mkdtemp(prefix="skill-factory-"))
    staged = staging_root / parsed.name
    try:
        for f in parsed.files:
            rel = Path(f.path)
            norm = (staged / rel).resolve()
            if rel.is_absolute() or not norm.is_relative_to(staged.resolve()):
                raise PermissionError(f"Path traversal blocked: {f.path}")
            norm.parent.mkdir(parents=True, exist_ok=True)
            norm.write_text(f.content, encoding="utf-8")

        if parsed.kind == "skill":
            # Register: must load cleanly through ADK's own skill loader.
            load_skill_from_dir(staged)

        base.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(target))
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    if parsed.kind == "skill":
        load_skill_from_dir(target)  # final registration check on disk

    outcome = {
        "persisted": True,
        "kind": parsed.kind,
        "name": parsed.name,
        "path": str(target),
        "files": [f.path for f in parsed.files],
    }
    ctx.state["persist_outcome"] = outcome
    logger.info("Persisted %s '%s' to %s", parsed.kind, parsed.name, target)
    return outcome


# --------------------------------------------------------------------------
# Respond (single terminal node)
# --------------------------------------------------------------------------


def respond(
    ctx: Context,
    node_input: Any = None,
    skill_answer: Optional[dict] = None,
    solve_result: Optional[dict] = None,
    reflect_result: Optional[dict] = None,
    sandbox_result: Optional[dict] = None,
    review: Optional[dict] = None,
    persist_outcome: Optional[dict] = None,
    intake: Optional[dict] = None,
) -> Event:
    """Composes the final user-facing answer from whatever path was taken."""
    parts: list[str] = []

    answer = skill_answer or solve_result
    if answer:
        parts.append(answer.get("summary") or "")
        if answer.get("artifacts"):
            parts.append("Artifacts: " + ", ".join(answer["artifacts"]))
    if skill_answer and intake and intake.get("matched_skill"):
        parts.append(f"_(handled by existing skill `{intake['matched_skill']}`)_")

    if persist_outcome and persist_outcome.get("persisted"):
        kind = persist_outcome["kind"]
        noun = "skill" if kind == "skill" else "MCP server scaffold"
        parts.append(
            f"**New capability persisted:** the {noun}"
            f" `{persist_outcome['name']}` is now registered at"
            f" `{persist_outcome['path']}` and will be used automatically for"
            " matching requests from now on."
        )
    elif review and not review.get("approved"):
        parts.append(
            "The drafted capability was **rejected** in review and has been"
            " discarded — nothing was persisted, and it will not be retried"
            " without new input."
            + (f" Reviewer feedback: {review.get('feedback')}" if review.get("feedback") else "")
        )
    elif reflect_result and not reflect_result.get("is_reusable_pattern"):
        parts.append(
            f"_(assessed as a one-off, no skill drafted:"
            f" {reflect_result.get('rationale', 'no rationale given')})_"
        )

    text = "\n\n".join(p for p in parts if p) or "Done."
    return Event(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        output=text,
    )

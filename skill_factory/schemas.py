"""Structured inputs/outputs exchanged between workflow nodes."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class IntakeResult(BaseModel):
    """Output of the intake node: classification + registry match."""

    task_restatement: str = Field(
        description=(
            "A complete, faithful restatement of the user's request,"
            " preserving every concrete detail (file paths, formats, column"
            " names, options). This is the brief handed to downstream agents."
        )
    )
    category: str = Field(
        description=(
            "Short category label for the request, e.g. 'csv-to-json',"
            " 'batch-rename', 'text-extraction', 'other'."
        )
    )
    matched_skill: Optional[str] = Field(
        default=None,
        description=(
            "Exact name of an existing registry skill that already covers"
            " this request, or null when no listed skill clearly applies."
        ),
    )


class SolveResult(BaseModel):
    """Output of the ad hoc Solve Task agent."""

    summary: str = Field(description="One-paragraph answer for the user.")
    approach: str = Field(
        description=(
            "How the task was solved: the procedure followed and any code"
            " executed, in enough detail that the approach could be repeated."
        )
    )
    artifacts: list[str] = Field(
        default_factory=list,
        description="Files or outputs produced (paths or short descriptions).",
    )


class ReflectResult(BaseModel):
    """Output of the reflect node: is this a repeatable pattern?"""

    is_reusable_pattern: bool = Field(
        description=(
            "True only when the solved task represents a generalizable,"
            " repeatable pattern worth encoding permanently — not a one-off."
        )
    )
    pattern_type: Optional[Literal["know-how", "tool-access"]] = Field(
        default=None,
        description=(
            "'know-how' when the gap is a missing procedure (make a skill);"
            " 'tool-access' when the gap is talking to an external service"
            " (scaffold an MCP server). Null when not a reusable pattern."
        ),
    )
    proposed_name: Optional[str] = Field(
        default=None,
        description=(
            "Proposed kebab-case name for the new skill or MCP server"
            " (letters, digits, hyphens only)."
        ),
    )
    rationale: str = Field(
        description="Why this is (or is not) worth turning into a capability."
    )


class DraftFile(BaseModel):
    path: str = Field(
        description=(
            "Path relative to the skill/server root, e.g. 'SKILL.md',"
            " 'references/formats.md', 'scripts/convert.py', 'server.py'."
        )
    )
    content: str = Field(description="Full file content.")


class Draft(BaseModel):
    """A drafted skill or MCP server scaffold, plus its trial scenario."""

    kind: Literal["skill", "mcp-server"]
    name: str = Field(description="kebab-case directory name.")
    files: list[DraftFile]
    example_scenario: str = Field(
        description="Short prose description of the example scenario tested."
    )
    test_script: str = Field(
        description=(
            "A standalone Python script that exercises the example scenario"
            " against the drafted files. It runs with the drafted files"
            " materialized at ./<relative paths> in the working directory."
            " It must print evidence of what it checked and exit non-zero"
            " (raise) on failure."
        )
    )


class ApprovalDecision(BaseModel):
    """Human reviewer's verdict at the HITL gate."""

    approved: bool = Field(
        description="True to persist the draft, false to discard it."
    )
    feedback: Optional[str] = Field(
        default=None, description="Optional reviewer comments."
    )


class FactoryState(BaseModel):
    """Session-state schema shared by the workflow's function nodes."""

    request: Optional[str] = None
    intake: Optional[dict] = None
    solve_result: Optional[dict] = None
    reflect_result: Optional[dict] = None
    draft: Optional[dict] = None
    draft_attempts: int = 0
    draft_feedback: Optional[str] = None
    sandbox_result: Optional[dict] = None
    review: Optional[dict] = None
    persist_outcome: Optional[dict] = None
    skill_answer: Optional[str] = None

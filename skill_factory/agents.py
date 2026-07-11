"""LLM agents used by the Skill Factory workflow.

Static graph nodes (intake, reflect) are single-turn agents with structured
output. The Solve/Draft specialists are task-mode agents (ADK Task API),
created fresh per dispatch and invoked dynamically via ctx.run_node from
function nodes — task-mode agents cannot be static workflow nodes.
"""

from __future__ import annotations

from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

from .config import MODEL, SEED_SKILLS_DIR, SKILLS_DIR
from .executors import get_code_executor
from .registry import format_skill_catalog, user_skill_registry
from .schemas import Draft, IntakeResult, ReflectResult, SolveResult
from .tools import run_python_code

_DOMAIN = (
    "You are part of Skill Factory, a personal automation agent for"
    " data-wrangling and file-conversion tasks (CSV<->JSON, reformatting,"
    " batch renaming, extraction, small transform scripts, and similar)."
)


# --------------------------------------------------------------------------
# Intake (static graph node)
# --------------------------------------------------------------------------


def _intake_instruction(_: ReadonlyContext) -> str:
    catalog = format_skill_catalog(user_skill_registry())
    return f"""{_DOMAIN}

You are the intake classifier. Read the user's request and produce the
structured intake result.

Currently registered skills (name: description):
{catalog}

Rules for matched_skill:
- Set it ONLY to a name that appears verbatim in the list above, and ONLY
  when that skill's description clearly covers the request.
- When the list is empty or nothing clearly applies, set it to null.
- Never invent a skill name.

task_restatement must preserve every concrete detail of the request
(file paths, formats, column names, delimiters, options). Downstream agents
see only your restatement, not the original message."""


intake_agent = LlmAgent(
    name="intake",
    model=MODEL,
    mode="single_turn",
    instruction=_intake_instruction,
    output_schema=IntakeResult,
)


# --------------------------------------------------------------------------
# Reflect (static graph node)
# --------------------------------------------------------------------------

reflect_agent = LlmAgent(
    name="reflect",
    model=MODEL,
    mode="single_turn",
    instruction=f"""{_DOMAIN}

You are the reflection step. You receive a task that was just solved ad hoc
(the request plus how it was solved). Decide whether it represents a
GENERALIZABLE, REPEATABLE pattern worth encoding as a permanent capability,
or a one-off.

Judge conservatively — most one-off requests should NOT become skills:
- is_reusable_pattern=true only when the same *kind* of task will plausibly
  recur with different inputs, and a written procedure or script would let
  it be done faster or more reliably next time.
- pattern_type='know-how' when what was missing is a procedure/recipe that
  can be written down (choose this for anything solvable with local code).
- pattern_type='tool-access' ONLY when the recurring need is talking to an
  external service/API that local code cannot reasonably cover, so a small
  MCP server is the right growth path.
- proposed_name: short kebab-case, e.g. 'csv-to-json' (letters/digits/hyphens).
""",
    output_schema=ReflectResult,
)


# --------------------------------------------------------------------------
# Solve Task agent (Task API, dispatched dynamically)
# --------------------------------------------------------------------------


def make_solve_agent() -> LlmAgent:
    return LlmAgent(
        name="solve_task",
        model=MODEL,
        mode="task",
        instruction=f"""{_DOMAIN}

You are the Solve Task specialist. Solve the request you are given, ad hoc.

- For anything requiring computation or file manipulation, WRITE PYTHON CODE
  and run it with the run_python_code tool (your only execution mechanism —
  there is no shell). Never claim you ran code you didn't run.
- Verify your result (e.g. re-read the output file, print a sample) before
  finishing.
- If the request references files you cannot access from the sandbox, say so
  in your summary and instead produce a ready-to-run script as the artifact,
  including its full source in `approach`.
- When done, call finish_task with an honest summary, the exact approach
  (including the code you ran), and the artifacts produced.""",
        tools=[run_python_code],
        output_schema=SolveResult,
    )


# --------------------------------------------------------------------------
# Skill executor (dispatched dynamically so it sees new skills immediately)
# --------------------------------------------------------------------------


def make_skill_executor_agent() -> LlmAgent:
    skills = [
        load_skill_from_dir(SKILLS_DIR / name)
        for name in sorted(user_skill_registry().list_frontmatters())
    ]
    return LlmAgent(
        name="skill_executor",
        model=MODEL,
        mode="task",
        instruction=f"""{_DOMAIN}

A registered skill has been matched to the user's request. Load that skill
with load_skill, follow its instructions exactly to complete the request,
and execute any computation via run_skill_script (for the skill's bundled
scripts) or run_python_code (for glue code). When done, call finish_task
with an honest summary of what you did and the results.""",
        tools=[
            SkillToolset(
                skills=skills,
                registry=user_skill_registry(),
                code_executor=get_code_executor(),
            ),
            run_python_code,
        ],
        output_schema=SolveResult,
    )


# --------------------------------------------------------------------------
# Draft agents (Task API; instructed via the seed meta-skills)
# --------------------------------------------------------------------------

_DRAFT_CONTRACT = """
You are running UNATTENDED inside a workflow:
- Never ask questions, never wait for input, never end your turn with plain
  text. Make reasonable decisions yourself.
- The guidance above is read-only reference material: do NOT try to run its
  scripts or follow its interactive/packaging/eval steps. You author files
  directly.
- Your ONLY way to complete this task is calling the finish_task tool.

Output contract — call finish_task with:
- kind and name exactly as briefed.
- files: every file of the draft, paths relative to the draft root.
- test_script: a STANDALONE Python script that trial-runs the example
  scenario. It executes in a sandbox where your drafted files are
  materialized at their relative paths in the working directory. It must:
  * use ONLY the Python standard library,
  * create its own tiny input fixtures (do not rely on outside files),
  * exercise the draft's main procedure/script end to end,
  * print what it checked, and raise (exit non-zero) on any failure.
The sandbox has no network access and nothing pip-installed."""


def make_skill_draft_agent() -> LlmAgent:
    seed = load_skill_from_dir(SEED_SKILLS_DIR / "skill-creator")
    return LlmAgent(
        name="skill_draft",
        model=MODEL,
        mode="task",
        instruction=f"""{_DOMAIN}

You are the Skill Draft specialist. A solved task was judged to be a
reusable 'know-how' pattern; author it as a permanent Agent Skill.

Authoring guidance from the 'skill-creator' meta-skill follows between the
BEGIN/END markers. Apply its principles for writing a good SKILL.md
(frontmatter, description, concise procedural body, progressive disclosure).

===== BEGIN skill-creator guidance =====
{seed.instructions}
===== END skill-creator guidance =====

Produce a spec-compliant skill directory:
- SKILL.md with YAML frontmatter: name (must equal the briefed kebab-case
  name), description (what it does + when to use it). Body: concise,
  procedural instructions generalized from the solved task — not a
  transcript of it.
- scripts/ with a parameterized, stdlib-only Python script when the pattern
  is executable; references/ only when genuinely needed. Keep it minimal.
{_DRAFT_CONTRACT}""",
        output_schema=Draft,
    )


def make_mcp_draft_agent() -> LlmAgent:
    seed = load_skill_from_dir(SEED_SKILLS_DIR / "mcp-builder")
    return LlmAgent(
        name="mcp_draft",
        model=MODEL,
        mode="task",
        instruction=f"""{_DOMAIN}

You are the MCP Draft specialist. A solved task was judged to need
recurring access to an external service; scaffold a small MCP server for it.

Guidance from the 'mcp-builder' meta-skill follows between the BEGIN/END
markers. Apply its Python (FastMCP) best practices.

===== BEGIN mcp-builder guidance =====
{seed.instructions}
===== END mcp-builder guidance =====

Produce a minimal server scaffold:
- server.py: FastMCP server with well-named, well-documented tools for the
  recurring need (clear docstrings, typed parameters, sensible errors).
- requirements.txt, and a README.md covering setup + client configuration.
Since the sandbox has no third-party packages, test_script must NOT import
the server's dependencies: instead it should verify the scaffold — e.g.
compile() each .py file to check syntax, and assert the expected tool
definitions appear in server.py.
{_DRAFT_CONTRACT}""",
        output_schema=Draft,
    )

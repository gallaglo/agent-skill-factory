"""The Skill Factory workflow graph (ADK Workflow Runtime).

    START -> intake -> <skill match?>
        use_skill -> execute_skill ------------------------------> respond
        solve     -> solve -> reflect -> <pattern?>
            no_pattern ------------------------------------------> respond
            draft_skill -> draft_skill_node --\
            draft_mcp   -> draft_mcp_node ----+-> sandbox_test
                sandbox: revise_skill/revise_mcp (bounded loop back to draft)
                sandbox: review -> hitl_gate (interrupt) -> <verdict?>
                    reject ----------------------------------------> respond
                    approve -> persist -------------------------> respond
"""

from __future__ import annotations

from google.adk.workflow import START, Workflow, node

from . import nodes
from .agents import intake_agent, reflect_agent

route_after_intake = node(nodes.route_after_intake)
execute_skill = node(nodes.execute_skill, rerun_on_resume=True)
solve = node(nodes.solve, rerun_on_resume=True)
route_after_reflect = node(nodes.route_after_reflect)
draft_skill = node(nodes.draft_skill, rerun_on_resume=True)
draft_mcp = node(nodes.draft_mcp, rerun_on_resume=True)
sandbox_test = node(nodes.sandbox_test, rerun_on_resume=True)
route_after_sandbox = node(nodes.route_after_sandbox)
hitl_gate = node(nodes.hitl_gate)  # rerun_on_resume=False: resume => output
route_after_review = node(nodes.route_after_review)
persist = node(nodes.persist)
respond = node(nodes.respond)

skill_factory_workflow = Workflow(
    name="skill_factory",
    description=(
        "Self-extending automation agent for data-wrangling and"
        " file-conversion tasks. Solves ad hoc requests, and grows a"
        " human-approved skill library out of repeatable patterns."
    ),
    # Note: no state_schema — ADK toolsets (e.g. SkillToolset) write internal
    # `_adk_*` session-state keys, which a strict schema would reject. The
    # state contract lives in schemas.FactoryState for documentation.
    edges=[
        (
            START,
            intake_agent,
            route_after_intake,
            {"use_skill": execute_skill, "solve": solve},
        ),
        (execute_skill, respond),
        (
            solve,
            reflect_agent,
            route_after_reflect,
            {
                "no_pattern": respond,
                "draft_skill": draft_skill,
                "draft_mcp": draft_mcp,
            },
        ),
        (draft_skill, sandbox_test),
        (draft_mcp, sandbox_test),
        (
            sandbox_test,
            route_after_sandbox,
            {
                "revise_skill": draft_skill,
                "revise_mcp": draft_mcp,
                "review": hitl_gate,
            },
        ),
        (
            hitl_gate,
            route_after_review,
            {"approve": persist, "reject": respond},
        ),
        (persist, respond),
    ],
)

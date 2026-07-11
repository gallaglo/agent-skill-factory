"""Entry point discovered by `adk run` / `adk web`.

Exports a resumable App: the HITL approval gate interrupts the workflow,
and the reviewer's reply resumes the same invocation.
"""

from __future__ import annotations

from google.adk.apps import App, ResumabilityConfig

from .workflow import skill_factory_workflow

root_agent = skill_factory_workflow

app = App(
    name="skill_factory",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)

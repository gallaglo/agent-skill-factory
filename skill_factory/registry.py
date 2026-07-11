"""Disk-backed skill registry.

The `skills/` directory IS the long-term memory: every lookup reads the
directory fresh, so a skill persisted by the approval flow is available to
the very next request without restarting the agent.
"""

from __future__ import annotations

import logging
from pathlib import Path

from google.adk.skills import Frontmatter
from google.adk.skills import list_skills_in_dir
from google.adk.skills import load_skill_from_dir
from google.adk.skills import Skill
from google.adk.skills import SkillRegistry

from .config import SEED_SKILLS_DIR
from .config import SKILLS_DIR

logger = logging.getLogger(__name__)


class LocalDirSkillRegistry(SkillRegistry):
    """SkillRegistry that re-reads one or more local directories on demand."""

    def __init__(self, *dirs: Path):
        self._dirs = [Path(d) for d in dirs]

    async def get_skill(self, *, name: str) -> Skill:
        for base in self._dirs:
            candidate = base / name
            if (candidate / "SKILL.md").exists() or (candidate / "skill.md").exists():
                return load_skill_from_dir(candidate)
        raise FileNotFoundError(
            f"Skill '{name}' not found in: {', '.join(str(d) for d in self._dirs)}"
        )

    async def search_skills(self, *, query: str) -> list[Frontmatter]:
        terms = [t for t in query.lower().split() if t]
        results = []
        for name, fm in self.list_frontmatters().items():
            haystack = f"{name} {fm.description or ''}".lower()
            if not terms or any(t in haystack for t in terms):
                results.append(fm)
        return results

    def list_frontmatters(self) -> dict[str, Frontmatter]:
        """Fresh listing of every valid skill across the registry dirs."""
        skills: dict[str, Frontmatter] = {}
        for base in self._dirs:
            if base.is_dir():
                for name, fm in list_skills_in_dir(base).items():
                    skills.setdefault(name, fm)
        return skills


def user_skill_registry() -> LocalDirSkillRegistry:
    """The user-grown registry (grows only via the HITL persist node)."""
    return LocalDirSkillRegistry(SKILLS_DIR)


def seed_skill_registry() -> LocalDirSkillRegistry:
    """The read-only meta skills: skill-creator and mcp-builder."""
    return LocalDirSkillRegistry(SEED_SKILLS_DIR)


def load_seed_skill(name: str) -> Skill:
    return load_skill_from_dir(SEED_SKILLS_DIR / name)


def format_skill_catalog(registry: LocalDirSkillRegistry) -> str:
    """Human/LLM-readable listing of the registry's current contents."""
    frontmatters = registry.list_frontmatters()
    if not frontmatters:
        return "(the skill registry is currently empty)"
    lines = []
    for name, fm in sorted(frontmatters.items()):
        lines.append(f"- {name}: {fm.description or '(no description)'}")
    return "\n".join(lines)

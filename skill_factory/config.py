"""Configuration for the Skill Factory agent.

Everything is driven by environment variables so the app runs unmodified
via `adk run` / `adk web`. Paths are anchored to the repository root
(the parent of this package) regardless of the current working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

SEED_SKILLS_DIR = Path(
    os.environ.get("SKILL_FACTORY_SEED_SKILLS_DIR", REPO_ROOT / "seed_skills")
)
"""Read-only meta skills (skill-creator, mcp-builder) vendored from
anthropics/skills. Used only to instruct the draft agents."""

SKILLS_DIR = Path(os.environ.get("SKILL_FACTORY_SKILLS_DIR", REPO_ROOT / "skills"))
"""User-grown skill registry. Starts empty; grows ONLY through the
HITL-approved persist node. This directory is the agent's long-term memory."""

MCP_SERVERS_DIR = Path(
    os.environ.get("SKILL_FACTORY_MCP_SERVERS_DIR", REPO_ROOT / "mcp-servers")
)
"""Generated MCP server scaffolds land here (also HITL-gated)."""

MODEL = os.environ.get("SKILL_FACTORY_MODEL", "")
"""Model for all LLM agents. Empty string means ADK's default Gemini model."""

MAX_DRAFT_ATTEMPTS = int(os.environ.get("SKILL_FACTORY_MAX_DRAFT_ATTEMPTS", "2"))
"""How many times a failing sandbox test may loop back into redrafting
before the draft is presented to the human reviewer as-is (failing)."""

SANDBOX_TEST_TIMEOUT = int(os.environ.get("SKILL_FACTORY_SANDBOX_TIMEOUT", "120"))
"""Seconds allowed for a drafted skill's sandbox trial run."""

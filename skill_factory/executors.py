"""Code-executor selection.

Safety requirement (spec §7): ALL code execution — ad hoc solving and
sandbox testing alike — goes through a BaseCodeExecutor, never inline in
the orchestrator process.

Selection order:
1. SKILL_FACTORY_SANDBOX_RESOURCE_NAME       -> AgentEngineSandboxCodeExecutor
   (an existing Agent Engine sandbox environment)
2. SKILL_FACTORY_AGENT_ENGINE_RESOURCE_NAME  -> AgentEngineSandboxCodeExecutor
   (sandbox created on demand inside that Agent Engine)
3. SKILL_FACTORY_EXECUTOR=agent-engine       -> AgentEngineSandboxCodeExecutor
   (auto-creates an Agent Engine; needs GOOGLE_CLOUD_PROJECT + Agent Platform API)
4. SKILL_FACTORY_EXECUTOR=unsafe-local       -> UnsafeLocalCodeExecutor
   (subprocess on this machine, NO isolation — local dev/demo only)

Anything else fails fast with setup instructions.
"""

from __future__ import annotations

import logging
import os

from google.adk.code_executors.base_code_executor import BaseCodeExecutor

logger = logging.getLogger(__name__)

_SETUP_HELP = """\
Skill Factory needs a sandboxed code executor. Configure one of:

  # Use an existing Agent Engine code-execution sandbox (recommended):
  export SKILL_FACTORY_SANDBOX_RESOURCE_NAME=projects/<p>/locations/<l>/reasoningEngines/<id>/sandboxEnvironments/<id>

  # Or let ADK create a sandbox inside an existing Agent Engine:
  export SKILL_FACTORY_AGENT_ENGINE_RESOURCE_NAME=projects/<p>/locations/<l>/reasoningEngines/<id>

  # Or auto-create an Agent Engine (requires GOOGLE_CLOUD_PROJECT and the
  # Agent Platform API enabled, service account with roles/aiplatform.user):
  export SKILL_FACTORY_EXECUTOR=agent-engine

  # Or, for local development ONLY (code runs unsandboxed on this machine):
  export SKILL_FACTORY_EXECUTOR=unsafe-local
"""

_executor: BaseCodeExecutor | None = None


def get_code_executor() -> BaseCodeExecutor:
    """Returns the process-wide code executor, building it on first use."""
    global _executor
    if _executor is None:
        _executor = _build_code_executor()
    return _executor


def _build_code_executor() -> BaseCodeExecutor:
    sandbox = os.environ.get("SKILL_FACTORY_SANDBOX_RESOURCE_NAME")
    engine = os.environ.get("SKILL_FACTORY_AGENT_ENGINE_RESOURCE_NAME")
    mode = os.environ.get("SKILL_FACTORY_EXECUTOR", "").strip().lower()

    if sandbox or engine or mode == "agent-engine":
        from google.adk.code_executors.agent_engine_sandbox_code_executor import (
            AgentEngineSandboxCodeExecutor,
        )

        logger.info(
            "Using AgentEngineSandboxCodeExecutor (sandbox=%s, engine=%s)",
            sandbox,
            engine,
        )
        return AgentEngineSandboxCodeExecutor(
            sandbox_resource_name=sandbox,
            agent_engine_resource_name=engine,
        )

    if mode == "unsafe-local":
        from google.adk.code_executors.unsafe_local_code_executor import (
            UnsafeLocalCodeExecutor,
        )

        logger.warning(
            "SKILL_FACTORY_EXECUTOR=unsafe-local: generated code will run"
            " UNSANDBOXED on this machine. Use only for local development."
        )
        return UnsafeLocalCodeExecutor()

    raise RuntimeError(_SETUP_HELP)

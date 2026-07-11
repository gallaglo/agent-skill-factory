"""Explicit code-execution tool for the specialist agents.

A thin FunctionTool over the configured BaseCodeExecutor, so models invoke
sandboxed execution as an ordinary tool call (spec §7: all code execution
goes through the code-execution tool, never in the orchestrator process).
"""

from __future__ import annotations

import asyncio

from google.adk.code_executors.code_execution_utils import CodeExecutionInput
from google.adk.tools.tool_context import ToolContext

from .executors import get_code_executor


async def run_python_code(code: str, tool_context: ToolContext) -> dict:
    """Executes a self-contained Python script in the sandboxed code executor.

    Args:
      code: Complete Python source. It must print() anything you want to
        see; the return value is not captured. Each call is independent —
        no variables survive between calls.

    Returns:
      dict with 'stdout' and 'stderr' from the execution.
    """
    executor = get_code_executor()
    result = await asyncio.to_thread(
        executor.execute_code,
        tool_context._invocation_context,
        CodeExecutionInput(code=code),
    )
    return {
        "stdout": (result.stdout or "")[-8000:],
        "stderr": (result.stderr or "")[-8000:],
    }

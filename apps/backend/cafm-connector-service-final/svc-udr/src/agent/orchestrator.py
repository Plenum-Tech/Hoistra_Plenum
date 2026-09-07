"""
UDR agent orchestrator — Anthropic tool-use loop.

Accepts a natural-language request from the main DeepAgents orchestrator,
routes through the 9 DB tools, and returns a structured result.
"""
import json
from typing import Any

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..services.database_service import DatabaseService
from ..agent.tools.definitions import TOOL_DEFINITIONS
from ..agent.tools.executor import ToolExecutor
from ..agent.prompts import SYSTEM_PROMPT
from ..core.logging import get_logger

log = get_logger(__name__)


class UDROrchestrator:
    def __init__(self, session: AsyncSession) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model
        self._session = session

    async def query(self, message: str) -> dict[str, Any]:
        """
        Process a natural-language database query.
        Returns {"reply": str, "success": bool, "tool_calls_made": int}.
        """
        svc = DatabaseService(self._session)
        executor = ToolExecutor(svc)

        messages: list[dict] = [{"role": "user", "content": message}]
        tool_calls_made = 0

        log.info("udr.query.start", message_preview=message[:120])

        for iteration in range(settings.max_agent_iterations):
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,  # type: ignore[arg-type]
                messages=messages,
            )

            log.info(
                "udr.agent.response",
                iteration=iteration,
                stop_reason=response.stop_reason,
                content_blocks=len(response.content),
            )

            if response.stop_reason == "end_turn":
                reply_text = next(
                    (b.text for b in response.content if hasattr(b, "text")),
                    "Query processed.",
                )
                # Strip markdown code fences if the model wrapped the JSON output
                stripped = reply_text.strip()
                if stripped.startswith("```"):
                    lines = stripped.splitlines()
                    inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                    stripped = "\n".join(inner).strip()
                # If the reply is valid JSON, return it parsed so the API response
                # contains a proper object under "reply" instead of an escaped string
                try:
                    reply_parsed: Any = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    reply_parsed = stripped
                log.info("udr.query.done", tool_calls_made=tool_calls_made)
                return {
                    "success": True,
                    "reply": reply_parsed,
                    "tool_calls_made": tool_calls_made,
                }

            if response.stop_reason == "tool_use":
                # Append the assistant turn (may include text + tool_use blocks)
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    tool_calls_made += 1
                    log.info("udr.tool.call", tool=block.name, input_keys=list(block.input.keys()))

                    result = await executor.execute(block.name, block.input)

                    log.info(
                        "udr.tool.result",
                        tool=block.name,
                        success=result.get("success"),
                    )

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })

                messages.append({"role": "user", "content": tool_results})
            else:
                # Unexpected stop reason — surface whatever text we have
                break

        # Fell through max iterations
        log.warning("udr.query.max_iterations", iterations=settings.max_agent_iterations)
        return {
            "success": False,
            "reply": "Query could not be completed within the allowed number of steps.",
            "tool_calls_made": tool_calls_made,
        }

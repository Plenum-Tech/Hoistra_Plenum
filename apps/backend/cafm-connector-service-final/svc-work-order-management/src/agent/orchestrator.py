"""
WO Orchestrator — GPT-powered conversational agent for work order management.

Accepts messages from any source (chat, email, PPM) and intelligently routes
through the available intelligence tools using OpenAI function calling.
The agent maintains full conversation history per session (DB-backed).
"""
import json
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from .prompts import SYSTEM_PROMPT
from .session_store import SessionStore
from .input_normalizer import InputNormalizer
from .tools.definitions import TOOL_DEFINITIONS
from .tools.executor import ToolExecutor
from ..config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

# Maximum tool-call iterations per user turn before giving up
_MAX_ITERATIONS = 15


class WOOrchestrator:
    """
    Conversational work order agent.

    Each call to .chat() continues (or starts) a session identified by session_id.
    The agent uses OpenAI function calling to call the right intelligence tools
    in the right order — no hardcoded step sequences.
    """

    def __init__(self, db_session) -> None:
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model
        self._db = db_session
        self._store = SessionStore(db_session)
        self._normalizer = InputNormalizer()

    # ── Public interface ───────────────────────────────────────────────────────

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        source: str = "chat",
    ) -> Dict[str, Any]:
        """
        Send a user message and get a reply.
        Creates a new session automatically if session_id is None or unknown.
        """
        # Resolve / create session
        if not session_id or not await self._store.exists(session_id):
            session_id = await self._store.create(source=source)
            log.info("orchestrator.new_session", session_id=session_id, source=source)

        messages = await self._store.load_messages(session_id)

        # Bootstrap system prompt on new conversations
        if not messages:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        messages.append({"role": "user", "content": message})

        reply, work_order = await self._agent_loop(messages, session_id)

        await self._store.save_messages(session_id, messages)

        return {"session_id": session_id, "reply": reply, "work_order": work_order}

    async def start_from_email(self, email_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Ingest a parsed email payload and start a work order conversation."""
        first_message = self._normalizer.from_email(email_payload)
        return await self.chat(first_message, source="email")

    async def start_from_ppm(self, schedule: Dict[str, Any]) -> Dict[str, Any]:
        """Trigger from a PPM schedule — typically creates WO automatically."""
        first_message = self._normalizer.from_ppm(schedule)
        return await self.chat(first_message, source="ppm")

    async def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Return conversation history, filtering out the system prompt."""
        messages = await self._store.load_messages(session_id)
        return [m for m in messages if m.get("role") != "system"]

    # ── Core agent loop ────────────────────────────────────────────────────────

    async def _agent_loop(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Run the GPT function-calling loop until the model produces a final
        text response (no more tool calls).

        Returns (reply_text, work_order_dict | None).
        The work_order dict is populated if create_work_order was called.
        """
        executor = ToolExecutor(self._db, session_id)
        work_order: Optional[Dict[str, Any]] = None

        for iteration in range(_MAX_ITERATIONS):
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.2,    # low temp for consistent, factual responses
                max_tokens=1024,
            )

            choice = response.choices[0]
            msg = choice.message

            # Append assistant turn — exclude None fields so history stays clean
            messages.append(msg.model_dump(exclude_none=True))

            # No tool calls → agent is done for this turn
            if not msg.tool_calls:
                reply = msg.content or ""
                log.info(
                    "orchestrator.turn_complete",
                    session_id=session_id,
                    iterations=iteration + 1,
                    has_work_order=work_order is not None,
                )
                return reply, work_order

            # Execute each tool call and append results
            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                log.info(
                    "orchestrator.tool_call",
                    session_id=session_id,
                    tool=fn_name,
                    iteration=iteration,
                )

                result = await executor.execute(fn_name, fn_args)

                # Track WO creation so we can return it in the response
                if fn_name == "create_work_order" and isinstance(result, dict) and result.get("success"):
                    work_order = result

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, default=str),
                })

        # Exceeded max iterations — return last assistant text if available
        log.warning("orchestrator.max_iterations_reached", session_id=session_id)
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                return m["content"], work_order
        return (
            "I'm sorry, I ran into an issue processing your request. Please try again.",
            work_order,
        )

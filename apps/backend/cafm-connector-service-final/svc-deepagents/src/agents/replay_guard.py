"""A turn re-run for measurement leaves nothing behind and changes nothing.

The skill lab (agents/skill_lab.py) replays real questions from Hoist Traces to score an
instruction change. A replay is the orchestrator answering for real - same tools, same reads -
so without a guard it would also write: a trace row billed to the company in Platform cost, a
chat thread in the Sessions list, chat memories learned from the replay's own answer, activity
log rows, and, for a question that asks for an action, a work order or an email.

While ``REPLAY`` is set the writers below skip, and any request that is not a read is refused
at the one HTTP client every tool shares:

* trace.begin                         -> no trace row (and so no platform cost)
* chat_threads.record_question/answer -> no thread, no fold, no memory learned from it
* chat_memories.learn_soon            -> nothing learned
* activity_log.record                 -> no activity rows
* http_client.request                 -> only GET goes out
"""
from __future__ import annotations

from contextvars import ContextVar

REPLAY: ContextVar[bool] = ContextVar("cafm_skill_lab_replay", default=False)

#: Methods a replay may send. Anything else would change data in another service.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_replay() -> bool:
    return bool(REPLAY.get())


class ReplayRefusedWrite(RuntimeError):
    """A replay asked another service to change something. The tool reports it as a failure."""

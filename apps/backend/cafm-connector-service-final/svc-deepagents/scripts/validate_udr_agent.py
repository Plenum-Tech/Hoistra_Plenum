"""Validate the UDR agent end to end: real sub-agent, real tools, real svc-udr, real database.

    # terminal 1, in svc-udr:       python scripts/serve_for_validation.py
    # terminal 2, in svc-deepagents: python scripts/validate_udr_agent.py [question ...]

For every question it prints how the orchestrator routes it, every tool the sub-agent called
in order, what the accuracy check (judge + invented-code check) made of the answer, and the
answer itself. For the golden questions it also runs one SQL statement against the same
database and checks that each true figure appears in the answer — a wrong headline count is a
FAIL even when the table under it is right. Exit code 1 on any FAIL.

Needs: DB_URL and ANTHROPIC_API_KEY (root .env), OPENAI_API_KEY and OPENAI_MODEL (the deployed
container app's values; put them in your shell, never in a file), and the validation svc-udr on
UDR_BASE_URL (default http://127.0.0.1:8006).

Why this exists: measured 17 Sep 2026, an answer whose rows were right said "8 parts, 6
stock-outs" over a table of 10 and 7, and "44 placeholder rows" where the query returned 38.
The trail cannot show that; only the number next to the truth can.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parents[3]
for candidate in (HERE / ".env", ROOT / ".env"):
    if candidate.exists():
        for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
os.environ.setdefault("UDR_BASE_URL", "http://127.0.0.1:8006")
os.environ.setdefault("ORCHESTRATOR_LLM_ROUTING", "1")
MODEL = os.environ.setdefault("OPENAI_MODEL", "gpt-5.6-terra")
for var in ("DB_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    if not os.environ.get(var):
        sys.exit(f"{var} is not set")
sys.path.insert(0, str(HERE))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from src.agents.agent_router import select_agent  # noqa: E402
from src.agents.meta_tools import _TaskRunner  # noqa: E402
from src.agents.orchestrator import DeepAgentOrchestrator as O  # noqa: E402
from src.agents.udr_response_evaluator import evaluate_udr_response  # noqa: E402
from src.http_client import caller_authorization  # noqa: E402

#: question -> list of (label, SQL returning one number, required). A required figure must appear
#: in the answer; an optional one may be left out but, if the answer gives a figure for it, the
#: judge is expected to have checked it. Number words ("seven") count as digits.
GOLDEN: dict[str, list[tuple[str, str, bool]]] = {
    # The agent may frame the headline as "47 records, 45 stock-outs" or "9 identified parts, 7
    # stock-outs" — both are true, so those are optional. The count of rows without a part code is
    # always stated and was wrong on four of five runs measured (44, 44, 39, 39 against 38): required.
    "Which spare parts are below their reorder level?": [
        ("parts below reorder", "SELECT count(*) FROM plenum_cafm.spare_parts WHERE stock_quantity < reorder_level", True),
        ("stock-outs, all", "SELECT count(*) FROM plenum_cafm.spare_parts WHERE stock_quantity < reorder_level AND stock_quantity = 0", False),
        ("stock-outs with a code", "SELECT count(*) FROM plenum_cafm.spare_parts WHERE stock_quantity < reorder_level AND stock_quantity = 0 AND COALESCE(part_code,'') <> ''", False),
        ("rows without a part code", "SELECT count(*) FROM plenum_cafm.spare_parts WHERE stock_quantity < reorder_level AND COALESCE(part_code,'') = ''", True),
    ],
    "Which spare parts need to be re-ordered, and which building assets use each of those parts?": [
        ("coded parts below reorder", "SELECT count(*) FROM plenum_cafm.spare_parts WHERE stock_quantity < reorder_level AND COALESCE(part_code,'') <> ''", False),
        ("stock-outs with a code", "SELECT count(*) FROM plenum_cafm.spare_parts WHERE stock_quantity < reorder_level AND stock_quantity = 0 AND COALESCE(part_code,'') <> ''", False),
        ("parts with a recorded asset link", "SELECT count(DISTINCT sp.id) FROM plenum_cafm.spare_parts sp JOIN plenum_cafm.work_order_parts wp ON wp.part_id = sp.id WHERE sp.stock_quantity < sp.reorder_level", False),
        ("rows without a part code", "SELECT count(*) FROM plenum_cafm.spare_parts WHERE stock_quantity < reorder_level AND COALESCE(part_code,'') = ''", True),
    ],
    "Who has been granted access to which buildings, and who approved it?": [
        ("access grants", "SELECT count(*) FROM plenum_cafm.user_buildings", True),
    ],
}

_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}


def _digits(text: str) -> str:
    """"seven stock-outs" and "7 stock-outs" are the same claim."""
    return re.sub(r"\b(" + "|".join(_WORDS) + r")\b", lambda m: _WORDS[m.group(1).lower()], text, flags=re.I)


def _short(x, n=140):
    s = x if isinstance(x, str) else json.dumps(x, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "…"


async def _truths(pairs):
    import asyncpg
    dsn = re.sub(r"^postgresql(\+\w+)?://", "postgresql://", os.environ["DB_URL"])
    conn = await asyncpg.connect(dsn, timeout=20)
    try:
        return [(label, await conn.fetchval(sql)) for label, sql in pairs]
    finally:
        await conn.close()


async def main(questions: list[str]) -> int:
    caller_authorization.set("Bearer validation")
    runner = _TaskRunner(os.environ["OPENAI_API_KEY"], MODEL)
    agent = runner._agents["udr"]
    failures = 0
    for q in questions:
        print("\n" + "=" * 100 + f"\nQ: {q}")
        routing = await select_agent(q)
        engine, _ = O._decide_dispatch(routing, q.lower())
        print(f"router: agent={routing.get('agent')} also={routing.get('also') or []} -> "
              + (f"{engine} engine" if engine else f"general loop, task({routing.get('agent')})"))
        t0 = time.perf_counter()
        out = await agent.ainvoke({"messages": [HumanMessage(q)]}, config={"recursion_limit": 40})
        msgs = out["messages"]
        trail: list[dict] = []
        pending: dict[str, dict] = {}
        for m in msgs:
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    rec = {"tool": tc["name"], "input": tc["args"], "output": None}
                    pending[tc["id"]] = rec
                    trail.append(rec)
                    print(f"  {len(trail):>2}. {tc['name']}({_short(tc['args'], 120)})")
            elif isinstance(m, ToolMessage):
                body = m.content if isinstance(m.content, str) else json.dumps(m.content, default=str)
                rec = pending.get(m.tool_call_id)
                if rec is not None:
                    try:
                        rec["output"] = json.loads(body)
                    except Exception:
                        rec["output"] = body
        answer = msgs[-1].content if isinstance(msgs[-1], AIMessage) else ""
        if not isinstance(answer, str):
            answer = " ".join(p.get("text", "") for p in answer if isinstance(p, dict))
        elapsed = time.perf_counter() - t0
        checked, verdict = await evaluate_udr_response(user_message=q, answer=answer, tool_calls=trail, llm=runner._llm)
        print(f"  accuracy check: evaluated={verdict.evaluated} passed={verdict.passed} grounded={verdict.grounded} "
              f"count_consistent={verdict.count_consistent} score={verdict.score:.2f}"
              + (f"\n    issues: {'; '.join(verdict.issues)[:300]}" if verdict.issues else ""))
        ok = True
        if q in GOLDEN:
            normalised = _digits(checked)
            for (label, _sql, required), (_l, truth) in zip(GOLDEN[q], await _truths([(l, s) for l, s, _r in GOLDEN[q]])):
                hit = truth is not None and re.search(rf"(?<![\d.]){re.escape(str(truth))}(?![\d.])", normalised) is not None
                if hit:
                    print(f"  PASS {label}: database says {truth}; stated")
                elif required:
                    ok = False
                    print(f"  FAIL {label}: database says {truth}; the answer does not state it (wrong figure, or missing)")
                else:
                    print(f"  skip {label}: database says {truth}; not stated (optional)")
        if not verdict.passed:
            ok = False
        failures += not ok
        print(f"  ({elapsed:.0f}s)  => {'OK' if ok else 'FAIL'}\nANSWER:\n" + "\n".join("  | " + l for l in checked.strip().splitlines()[:30]))
    print(f"\n{len(questions) - failures}/{len(questions)} questions validated")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:] or list(GOLDEN))))

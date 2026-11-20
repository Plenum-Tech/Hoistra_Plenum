#!/usr/bin/env python3
"""Load a company's chat memories (JSONL export) for the hybrid test page.

Writes the Memorilla documents file (one {"text": ...} per line, in memorilla.memory_text's
wording, so `plenum-memory add` stores them as the orchestrator would) and builds the company's
cognee graph through this service's own ingest path (cognee_graph._graph_texts + _cognee_add:
active company facts and corrections only, never preferences). See README.md in this folder.

    python load.py --memories chat_memories.jsonl --org <org uuid> \\
        --memorilla-out memorilla_docs.jsonl [--limit N] [--no-graph]

Each input line: {"org": "<uuid>", "kind": "fact|correction|preference", "text": "...",
"subject": "..." or null}.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # svc-deepagents
os.environ.setdefault("COGNEE_MEMORY_COMPANIES", "*")
os.environ.setdefault("DB_URL", "postgresql+asyncpg://unused:unused@nowhere.invalid:5432/unused")

from src.services import cognee_graph as cg  # noqa: E402
from src.services import memorilla  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--memories", required=True, help="chat memories JSONL export")
    ap.add_argument("--org", required=True, help="the company's organization id")
    ap.add_argument("--memorilla-out", help="write Memorilla's documents file here")
    ap.add_argument("--limit", type=int, default=0, help="only the first N memories (quick tests)")
    ap.add_argument("--no-graph", action="store_true", help="skip building the cognee graph")
    a = ap.parse_args()

    with open(a.memories, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = [r for r in rows if r.get("org") == a.org]
    if a.limit:
        rows = rows[: a.limit]
    if not rows:
        sys.exit(f"no memories for org {a.org} in {a.memories}")
    print(f"{len(rows)} memories for {a.org}")

    if a.memorilla_out:
        with open(a.memorilla_out, "w", encoding="utf-8") as f:
            for r in rows:
                text = memorilla.memory_text(r["kind"], r["text"], r.get("subject"),
                                             personal=r["kind"] == "preference")
                f.write(json.dumps({"text": text}) + "\n")
        print(f"Memorilla documents -> {a.memorilla_out}  (load: plenum-memory add --user "
              f"{memorilla.collection(a.org, 'memories')} --no-chunk {a.memorilla_out})")

    if a.no_graph:
        return
    items = [{"kind": r["kind"], "text": r["text"], "subject": r.get("subject"),
              "personal": r["kind"] == "preference", "active": True} for r in rows]
    texts = cg._graph_texts(items)
    print(f"building the cognee graph from {len(texts)} company facts/corrections "
          f"(dataset {cg._dataset(a.org)}) ...", flush=True)
    t = time.monotonic()
    await cg._cognee_add(a.org, texts)
    print(f"graph built in {(time.monotonic() - t) / 60:.1f} min")


if __name__ == "__main__":
    asyncio.run(main())

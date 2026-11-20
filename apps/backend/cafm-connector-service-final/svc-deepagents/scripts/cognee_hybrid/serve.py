#!/usr/bin/env python3
# ruff: noqa: E501 — the page's HTML/CSS/JS below is kept on long lines
"""Test page for the Memorilla + cognee hybrid: Memorilla alone vs Memorilla + graph-linked
memories, side by side, with the full trace of how each answer was produced.

Runs this service's own code (memorilla.search_memories -> chat_memories._ranked_by_memorilla ->
format_recall, and cognee_graph.graph_context -> linked_memories) on a company's chat memories
exported to JSONL. Nothing touches the Hoistra database. See README.md in this folder.

    HYBRID_ORG=<org uuid> HYBRID_MEMORIES=/path/chat_memories.jsonl python serve.py
    # then open http://127.0.0.1:8004/
"""
import asyncio
import contextvars
import json
import os
import sys
import time
from pathlib import Path

SVC = Path(__file__).resolve().parents[2]          # svc-deepagents
sys.path.insert(0, str(SVC))
os.environ.setdefault("COGNEE_MEMORY_MODE", "live")      # the page shows what live mode adds
os.environ.setdefault("COGNEE_MEMORY_COMPANIES", "*")
os.environ.setdefault("DB_URL", "postgresql+asyncpg://unused:unused@nowhere.invalid:5432/unused")
ORG = os.environ["HYBRID_ORG"]
MEMORIES = os.environ["HYBRID_MEMORIES"]

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from src.agents import trace  # noqa: E402
from src.services import chat_memories as cm  # noqa: E402
from src.services import cognee_graph as cg  # noqa: E402
from src.services import memorilla  # noqa: E402

GRAPH_ORG = ORG                 # load.py builds the company's graph under its own org id
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
BASE = os.environ.get("OPENAI_API_BASE", "").strip() or None      # a local server (Ollama) works
EFFORT = os.environ.get("COGNEE_LLM_REASONING_EFFORT", "").strip()
SYSTEM = ("You are Hoistra, a facilities-management assistant. Notes from earlier conversations may "
          "follow; use them when they bear on the question. Do not invent facts: if the notes do not "
          "say, say you do not know. Answer in a few sentences.")

MEMS = [{"id": str(n), "kind": r["kind"], "text": r["text"], "subject": r.get("subject"),
         "user_id": "me" if r["kind"] == "preference" else None, "created_by_email": None, "created_at": None}
        for n, r in enumerate(json.loads(line) for line in open(MEMORIES, encoding="utf-8") if line.strip())
        if r["org"] == ORG]

# ── recording: what the branch's code did, captured as it runs ────────────────────────────────
_rec: contextvars.ContextVar[dict] = contextvars.ContextVar("rec")
_orig_search = memorilla.search_memories


async def _recording_search(org, question):
    t = time.monotonic()
    hits = await _orig_search(org, question)
    _rec.get()["memorilla_raw"] = {"ms": int((time.monotonic() - t) * 1000), "hits": hits}
    return hits


async def _visible(include_pending=False):
    return list(MEMS)


def _span(name, inputs, output, **kw):
    _rec.get().setdefault("spans", []).append({"name": name, "inputs": inputs, "output": output, **kw})


memorilla.search_memories = _recording_search   # the real call, recorded
cm.visible = _visible                            # the export stands in for the memories table
trace.on_memory = _span                          # the spans the orchestrator would record
llm = AsyncOpenAI(base_url=BASE, api_key=os.environ.get("OPENAI_API_KEY") or "local")
app = FastAPI()


class Ask(BaseModel):
    question: str


@app.on_event("startup")
async def _warm() -> None:
    """The first cognee read in a process is cold (> the 3 s cap); do it before anyone asks."""
    _rec.set({})
    saved = cg.GRAPH_TIMEOUT_S
    cg.GRAPH_TIMEOUT_S = 60
    try:
        await cg.graph_context("warm-up", GRAPH_ORG)
    finally:
        cg.GRAPH_TIMEOUT_S = saved


async def _answer(block: str, question: str) -> dict:
    t = time.monotonic()
    user = (block + "\n\n---\n\n**Current user message:**\n" + question) if block else question
    extra = {"extra_body": {"reasoning_effort": EFFORT}} if EFFORT else {}
    r = await llm.chat.completions.create(model=MODEL, max_completion_tokens=700, **extra,
                                          messages=[{"role": "system", "content": SYSTEM},
                                                    {"role": "user", "content": user}])
    u = r.usage
    return {"answer": r.choices[0].message.content or "", "seconds": round(time.monotonic() - t, 1),
            "prompt": {"system": SYSTEM, "user": user},
            "tokens": {"in": getattr(u, "prompt_tokens", None), "out": getattr(u, "completion_tokens", None)}}


def _ranking_trace(q: str, raw: list, ranked: list, hits: list) -> dict:
    """Bala's ranking (chat_memories._ranked_by_memorilla + recall()'s cut), step by step."""
    rows, seen = [], set()
    for h in raw:
        ht, s = h.get("text") or "", float(h.get("similarity") or 0)
        m = next((m for m in MEMS if (m.get("text") or "") and m["text"] in ht), None)
        if m is None:
            why = "no visible memory has this text (dropped)"
        elif m["id"] in seen:
            why = "same memory as an earlier hit (dropped)"
        elif s < memorilla.MEMORY_MIN:
            why = f"below MEMORY_MIN {memorilla.MEMORY_MIN} -> scored 0"
        else:
            why = "kept at its similarity"
        if m is not None:
            seen.add(m["id"])
        rows.append({"similarity": round(s, 3), "text": ht, "memory": m["id"] if m else None, "why": why})
    raw_ids = {r["memory"] for r in rows if r["memory"]}
    named = [{"memory": m["id"], "text": m["text"], "score": round(s, 3)} for s, m in ranked
             if m["id"] not in raw_ids]
    floor = min(cm.RECALL_MIN, memorilla.MEMORY_MIN)
    return {"collection": memorilla.collection(ORG, "memories"), "k": memorilla.MEMORY_K,
            "raw": rows, "named_in_question": named,
            "rule": f"top {cm.RECALL_TOP} by score, score >= {floor} and > 0 (named in question = {cm.RECALL_NAMED})",
            "recalled": [{"memory": h["id"], "score": h["score"], "kind": h["kind"], "text": h["text"]} for h in hits]}


def _graph_trace(q: str, hits: list, graph: dict | None, linked: list) -> dict:
    """cognee_graph.linked_texts + linked_memories, step by step."""
    if graph is None:
        return {"off": True}
    strong, weak = cg._anchors(q, hits)
    anchors = strong | weak
    nodes = graph.get("nodes") or {}
    edges, links, seen = [], [], []
    for src, rel, dst, _ in graph.get("edges") or []:
        if not cg._is_chunk((src, rel, dst, "")):
            edges.append({"edge": f"{src} --{rel}--> {dst}", "why": "link between two things (not used)"})
            continue
        hit = next((a for a in sorted(anchors, key=len, reverse=True) if a and a in dst.lower()), None)
        content = nodes.get(src) or ""
        if not hit:
            why = "the thing it mentions matches no anchor"
        elif not content:
            why = "fact node has no text"
        elif content in seen:
            why = "fact already linked"
        else:
            why = f"links this fact (mentions '{dst}', anchor '{hit}')"
            seen.append(content)
            links.append(content)
        edges.append({"edge": f"{src[:70]} --{rel}--> {dst}", "why": why})
    have = {h["id"] for h in hits}
    mapped = []
    added = {m["id"] for m in linked}
    for c in links:
        m = next((m for m in MEMS if m["kind"] in cg.GRAPH_KINDS and m["user_id"] is None
                  and cg.fact_text(m["kind"], m["text"], m.get("subject")) in c), None)
        if m is None:
            status = "no visible company fact has exactly this text (not added)"
        elif m["id"] in have:
            status = "Memorilla already recalled it (not added again)"
        elif m["id"] in added:
            status = "ADDED to the recall"
        else:
            status = f"over the cap of {cg.LINKED_CAP} (not added)"
        mapped.append({"fact": c, "memory": m["id"] if m else None, "status": status})
    return {"dataset": cg._dataset(GRAPH_ORG), "ms": graph.get("latency_ms"), "error": graph.get("error"),
            "edges_total": len(graph.get("edges") or []), "nodes_total": len(nodes),
            "anchors": {"strong": sorted(strong), "weak": sorted(weak)}, "edges": edges, "mapped": mapped}


@app.post("/ask")
async def ask(body: Ask):
    _rec.set({})
    q = body.question.strip()
    t = time.monotonic()
    ranked, graph = await asyncio.gather(cm._ranked_by_memorilla(q, MEMS, ORG, cm._QuestionNames(q)),
                                         cg.graph_context(q, GRAPH_ORG))
    recall_s = round(time.monotonic() - t, 1)
    floor = min(cm.RECALL_MIN, memorilla.MEMORY_MIN)
    hits = [m | {"score": round(s, 3)} for s, m in ranked[:cm.RECALL_TOP] if s >= floor and s > 0]
    linked = await cg.linked_memories(q, hits, graph)
    a_mem, a_hyb = await asyncio.gather(_answer(cm.format_recall(hits), q), _answer(cm.format_recall(hits + linked), q))
    rec = _rec.get()
    raw = (rec.get("memorilla_raw") or {})
    return {"recall_s": recall_s, "graph_ms": (graph or {}).get("latency_ms"), "graph_error": (graph or {}).get("error"),
            "hits": [{"text": h["text"], "score": h["score"]} for h in hits], "linked": [{"text": m["text"]} for m in linked],
            "memorilla": a_mem, "hybrid": a_hyb, "model": MODEL,
            "trace": {"memorilla_ms": raw.get("ms"), "endpoint": memorilla.url() + "/v1/memory/search",
                      "ranking": _ranking_trace(q, raw.get("hits") or [], ranked, hits),
                      "graph": _graph_trace(q, hits, graph, linked), "spans": rec.get("spans", [])}}


@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Memorilla + cognee test</title>
<style>
:root{--ink:#14181f;--muted:#5b6472;--line:#e3e6eb;--bg:#f6f7f9;--card:#fff;--accent:#1f5eff;--add:#0f7a4a;--addbg:#eaf6ef;--drop:#9a3412;--code:#f3f4f6}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}
main{max-width:1150px;margin:0 auto;padding:24px 16px 60px}h1{font-size:20px;margin:0 0 2px}.sub{color:var(--muted);margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:14px}
.row{display:flex;gap:10px;flex-wrap:wrap}input{flex:1;min-width:240px;font:inherit;padding:9px 10px;border:1px solid var(--line);border-radius:8px}
button{font:inherit;padding:9px 16px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}button:disabled{opacity:.5}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.chip{font-size:13px;padding:5px 10px;border:1px solid var(--line);border-radius:99px;background:#fff;color:var(--ink);cursor:pointer}
.q{font-weight:600;margin-bottom:8px}.meta{font-size:13px;color:var(--muted)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px}@media(max-width:760px){.cols{grid-template-columns:1fr}}
.col{border:1px solid var(--line);border-radius:8px;padding:10px;min-width:0}.col h3{font-size:14px;margin:0 0 6px}
.ans{white-space:pre-wrap}ul{margin:6px 0 0;padding-left:18px;font-size:13px}li{margin:2px 0}
li.add{background:var(--addbg);color:var(--add);border-radius:4px;padding:1px 4px;list-style:none;margin-left:-18px}
details{margin-top:8px}summary{cursor:pointer;font-size:13px;color:var(--muted)}
.trace{margin-top:12px;border-top:1px solid var(--line);padding-top:8px}.trace>summary{font-size:14px;color:var(--ink);font-weight:600}
.step{border-left:3px solid var(--line);padding:6px 0 6px 12px;margin:10px 0}.step h4{margin:0 0 4px;font-size:14px}
.step p{margin:2px 0;font-size:13px;color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:4px}td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600}td.num{white-space:nowrap;font-variant-numeric:tabular-nums}
.ok{color:var(--add)}.no{color:var(--drop)}
pre{white-space:pre-wrap;word-break:break-word;font:12px/1.45 ui-monospace,Menlo,monospace;background:var(--code);border:1px solid var(--line);border-radius:6px;padding:8px;max-height:360px;overflow:auto;margin:4px 0}
.tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:99px;background:var(--code);margin-right:4px}
</style></head><body><main>
<h1>Memorilla + cognee — test</h1>
<p class="sub">This company's chat memories (JSONL export). Left: Bala's Memorilla recall alone. Right: the same recall plus the memories the cognee graph links to the question (green). Same model, same instructions. Open "How this answer was produced" under any answer for the full trace.</p>
<div class="card"><div class="row"><input id="q" placeholder="Ask about the estate…"><button id="go">Ask</button></div><div class="chips" id="chips"></div></div>
<div id="out"></div>
</main><script>
const QS=["Which vendor should I sort out first?","AHU-3 at Bishopsgate needs a service. Who do we send, and is anything stopping us?",
"We want fire door repairs at Bishopsgate Tower next week. Anything I should know first?",
"We need the L12 riser compartmentation fixed at Bishopsgate. Who is our fire contractor and can we use them?",
"Which vendor is blocked at Bishopsgate Tower, and why?","Since when has the Bishopsgate Fire Risk Assessment been lapsed?",
"Which certificate is lapsed at Manchester Town Hall?","How do I like my answers laid out?"];
const $=id=>document.getElementById(id);
QS.forEach(q=>{const b=document.createElement('button');b.className='chip';b.textContent=q;b.onclick=()=>{$('q').value=q;ask()};$('chips').append(b)});
$('q').addEventListener('keydown',e=>{if(e.key==='Enter')ask()});$('go').onclick=ask;
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function list(items,cls){const u=el('ul');items.forEach(i=>u.append(el('li',cls,i)));return u}
function table(head,rows){const t=el('table');const h=el('tr');head.forEach(x=>h.append(el('th',null,x)));t.append(h);
 rows.forEach(r=>{const tr=el('tr');r.forEach(c=>{const td=el('td',c&&c.cls||null,c&&c.t!=null?c.t:c);tr.append(td)});t.append(tr)});return t}
function step(n,title,...kids){const s=el('div','step');s.append(el('h4',null,n+'. '+title));kids.forEach(k=>s.append(typeof k==='string'?el('p',null,k):k));return s}
function renderTrace(r){
 const T=r.trace,R=T.ranking,G=T.graph;const d=el('details','trace');d.append(el('summary',null,'How this answer was produced'));
 d.append(step(1,'Memorilla search (Bala\\'s server)',`POST ${T.endpoint}  ·  collection ${R.collection}  ·  k=${R.k}  ·  ${T.memorilla_ms} ms`,
  'The question is embedded with Qwen3-Embedding-4B and compared with every stored memory (cosine).',
  table(['similarity','stored entry','what Bala\\'s ranking did'],R.raw.map(x=>[{t:x.similarity,cls:'num'},x.text,{t:x.why,cls:x.why.startsWith('kept')?'ok':'no'}]))));
 const named=R.named_in_question.length?table(['score','memory named in the question'],R.named_in_question.map(x=>[{t:x.score,cls:'num'},x.text])):el('p',null,'None.');
 d.append(step(2,'Bala\\'s ranking → what Memorilla recalls','Memories whose name (building, vendor…) appears in the question are lifted to at least '+R.rule.split('= ').pop(),named,
  'Rule: '+R.rule,table(['score','kind','recalled memory'],R.recalled.map(x=>[{t:x.score,cls:'num'},x.kind,x.text]))));
 if(G.off){d.append(step(3,'cognee graph','Graph off for this company.'))}else{
 d.append(step(3,'cognee graph read (run at the same time as step 1)',`dataset ${G.dataset}  ·  ${G.ms} ms  ·  ${G.edges_total} connections, ${G.nodes_total} nodes`+(G.error?'  ·  ERROR '+G.error:''),
  'One search, no LLM call: the question is embedded and cognee returns the stored facts and things around it.'));
 d.append(step(4,'Anchors — what a connection must touch','Strong (names and codes in the question and in step 2\\'s memories): '+(G.anchors.strong.join(', ')||'—'),
  'Weak (the question\\'s own words): '+(G.anchors.weak.join(', ')||'—')));
 d.append(step(5,'Which connections link a stored fact',table(['connection','result'],G.edges.map(x=>[x.edge,{t:x.why,cls:x.why.startsWith('links')?'ok':'no'}]))));
 d.append(step(6,'Linked facts mapped back to memories the person may see (cap '+4+')',
  G.mapped.length?table(['linked fact (as the graph keeps it)','memory','result'],G.mapped.map(x=>[x.fact,x.memory||'—',{t:x.status,cls:x.status.startsWith('ADDED')?'ok':'no'}])):el('p',null,'No fact linked.')));
 T.spans.forEach(s=>{const p=el('pre');p.textContent=JSON.stringify({name:s.name,inputs:s.inputs,output:s.output,ok:s.ok,error:s.error,latency_ms:s.latency_ms},null,2);
  d.append(step(7,'The span the orchestrator records in the run trace ("'+s.name+'")',p))})}
 [['Memorilla alone',r.memorilla],['Memorilla + cognee',r.hybrid]].forEach(([name,a],i)=>{
  const sp=el('pre');sp.textContent=a.prompt.system;const up=el('pre');up.textContent=a.prompt.user;
  d.append(step(8+i*0.5===8?'8a':'8b','Exact prompt sent — '+name,el('p',null,'System:'),sp,el('p',null,'User:'),up,
   el('p',null,`Model ${r.model}  ·  ${a.seconds} s  ·  ${a.tokens.in} tokens in, ${a.tokens.out} out`)))});
 return d}
async function ask(){const q=$('q').value.trim();if(!q)return;$('go').disabled=true;
 const card=el('div','card');card.append(el('div','q',q));const meta=el('div','meta','thinking…');card.append(meta);$('out').prepend(card);
 try{const r=await (await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})})).json();
  meta.textContent=`recall ${r.recall_s}s (graph ${r.graph_ms} ms${r.graph_error?', '+r.graph_error:''}) · memorilla ${r.hits.length} memories · graph added ${r.linked.length}`;
  const cols=el('div','cols');
  const a=el('div','col');a.append(el('h3',null,'Memorilla alone · '+r.memorilla.seconds+' s'),el('div','ans',r.memorilla.answer));
  const da=el('details');da.append(el('summary',null,'Recalled memories ('+r.hits.length+')'),list(r.hits.map(h=>h.text+'  ['+h.score+']')));a.append(da);
  const b=el('div','col');b.append(el('h3',null,'Memorilla + cognee · '+r.hybrid.seconds+' s'),el('div','ans',r.hybrid.answer));
  const db=el('details');db.open=r.linked.length>0;const u=list(r.hits.map(h=>h.text));r.linked.forEach(m=>u.append(el('li','add','+ '+m.text)));
  db.append(el('summary',null,'Recalled memories ('+r.hits.length+' + '+r.linked.length+' from the graph)'),u);b.append(db);
  cols.append(a,b);card.append(cols,renderTrace(r));
 }catch(e){meta.textContent='failed: '+e}
 $('go').disabled=false}
</script></body></html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("HYBRID_PORT", "8004")), log_level="warning")

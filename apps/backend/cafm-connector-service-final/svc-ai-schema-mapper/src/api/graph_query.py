"""Schema relationship-graph query endpoint.

Exposes the PK/FK graph query-expansion (matchers/schema_graph.py) so the deep-agents
orchestrator can plan graph-driven RAG retrieval: a natural-language question → the
related entity cluster (tables + FK paths + primary keys) to pull records + chunks for.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from ..matchers.schema_graph import build_schema_graph, expand_query

router = APIRouter(prefix="/api/graph", tags=["schema graph"])


class ExpandRequest(BaseModel):
    query: str
    depth: int = 1
    max_tables: int = 15


@router.post("/expand-query")
async def expand_query_route(req: ExpandRequest) -> dict:
    """A question → related table cluster + FK relationships + primary keys."""
    return expand_query(req.query, depth=req.depth, max_tables=req.max_tables)


@router.get("/schema")
async def schema_route() -> dict:
    """The full table → {pk, fks, referenced_by} relationship graph."""
    g = build_schema_graph()
    return {
        "table_count": len(g["tables"]),
        "tables": {
            t: {"primary_key": m["pk"], "foreign_keys": m["fks"]}
            for t, m in g["tables"].items()
        },
    }

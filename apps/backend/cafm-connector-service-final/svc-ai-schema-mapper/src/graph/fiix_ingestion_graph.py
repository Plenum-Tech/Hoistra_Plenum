"""3-node LangGraph pipeline for Fiix data ingestion.

  fiix_fetch_node → fiix_preprocess_node → fiix_write_node

No HITL gates.  The graph runs to completion in a single ainvoke() call.
Errors in any node set state["status"] = "failed" and halt the run via
a conditional edge back to END.
"""

from langgraph.graph import StateGraph, END

from .fiix_state import FiixIngestionState
from .nodes.fiix_fetch_node import fiix_fetch_node
from .nodes.fiix_preprocess_node import fiix_preprocess_node
from .nodes.fiix_write_node import fiix_write_node


def _is_failed(state: FiixIngestionState) -> str:
    """Conditional edge: route to END early if a node set status='failed'."""
    return "end" if state.get("status") == "failed" else "continue"


def build_fiix_ingestion_graph(checkpointer=None):
    """
    Build and compile the 3-node Fiix ingestion graph.

    Args:
        checkpointer: Optional LangGraph checkpointer (AsyncPostgresSaver).
                      Pass None for tests or when checkpointing is not needed.

    Returns:
        Compiled LangGraph CompiledGraph ready for ainvoke().
    """
    builder = StateGraph(FiixIngestionState)

    # Each node wrapped with input/output processing logs (graph/node_logging.py).
    from .node_logging import wrap_node
    wrap_node(builder, "fiix_fetch",      fiix_fetch_node)
    wrap_node(builder, "fiix_preprocess", fiix_preprocess_node)
    wrap_node(builder, "fiix_write",      fiix_write_node)

    builder.set_entry_point("fiix_fetch")

    # After fetch: abort on failure, else preprocess
    builder.add_conditional_edges(
        "fiix_fetch",
        _is_failed,
        {"end": END, "continue": "fiix_preprocess"},
    )

    # After preprocess: abort on failure, else write
    builder.add_conditional_edges(
        "fiix_preprocess",
        _is_failed,
        {"end": END, "continue": "fiix_write"},
    )

    # Write always goes to END (it sets status = "complete" or "failed" itself)
    builder.add_edge("fiix_write", END)

    if checkpointer:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()

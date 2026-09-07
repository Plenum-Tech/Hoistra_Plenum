"""Feature 7 — UDR mechanics (value-centric pipeline primitives).

This package holds the net-new, *data-row based* building blocks for Feature 7:
primary-key detection, cell-value format profiling, value overlap, referential
integrity, and PK / Foreign-Key / Shared-Attribute classification — plus the
canonical table/column metadata builders.

They are deliberately PURE (operate on ``list[dict]`` records, no DB/LLM) so they
can be unit-tested in isolation and reused by BOTH the migration (data) path and
the Fiix path (after its data is ingested). See FEATURE7_UDR_UNDERSTANDING.md.
"""

from .primitives import (  # noqa: F401
    THRESHOLDS,
    detect_primary_key,
    primary_key_report,
    is_primary_key_column,
    null_rate,
    uniqueness,
    cell_format,
    column_format,
    value_overlap,
    referential_integrity,
    classify_columns,
    build_table_metadata,
    build_column_metadata,
)
from .validation import (  # noqa: F401
    run_test1_chunk_pk,
    run_test2_column_fk,
    TEST1_PASS_RATE_MIN,
    TEST2_FAIL_RATE_MAX,
)
from .graph import build_relationship_graph, build_work_cloud, neighbors  # noqa: F401
from .unique_tables import (  # noqa: F401
    NAME_SIM_AUTO,
    METADATA_SIM_MIN,
    apply_consolidation,
    identify_unique_tables,
    metadata_similarity,
    table_name_similarity,
)
from .table_resolution import (  # noqa: F401
    build_table_resolution,
    classify_table_method,
)
from .column_intelligence import (  # noqa: F401
    build_column_intelligence,
    canonical_name_for_group,
)
from .preprocessing import (  # noqa: F401
    nan_stats,
    remove_exact_duplicate_rows,
    find_column_merge_candidates,
    find_cross_table_merge_candidates,
    find_same_name_divergent_columns,
    propose_column_name,
    preprocessing_summary,
)
from .reference_tables import (  # noqa: F401
    build_reference_table,
    promote_shared_attribute,
    remediate_test1_failures,
)
from .activity import (  # noqa: F401
    UDR_STAGES,
    notif_color,
    format_activity_timestamp,
    build_udr_activity_entry,
    build_stage_meta,
    build_summary_metrics,
)
from .activity_persist import (  # noqa: F401
    NOTIF_PRIORITY,
    entry_to_columns,
    record_activity,
    list_activity,
    get_activity,
    mark_activity_read,
    unread_summary,
)
from .pipeline import run_udr_pipeline, UdrRunResult  # noqa: F401
from .persistence import (  # noqa: F401
    persist_run_graph,
    persist_relationships,
    persist_udr_run,
    query_work_cloud,
    query_instance_work_cloud,
)
from .instance_cloud import build_traversal_plan, is_safe_identifier  # noqa: F401
from .semantic_discovery import (  # noqa: F401
    LLM_INFERRED,
    discover_relationships,
    normalize_discovered_edge,
    normalize_discovered_edges,
)
from .emit import emit_udr_activity  # noqa: F401
from .actions import (  # noqa: F401
    ACTION_TIMEOUT_SECONDS,
    MAX_OPTIONS,
    build_inline_action,
    build_pending_response_entry,
    deadline_from,
    is_overdue,
    is_valid_option,
    normalize_options,
    select_overdue,
)
from .actions_persist import (  # noqa: F401
    action_to_columns,
    record_action,
    list_actions,
    list_pending_actions,
    resolve_action,
    sweep_timeouts,
)
from .suggestions import (  # noqa: F401
    MAX_FOLLOW_UPS,
    MAX_REFINEMENTS,
    build_suggestions,
    udr_suggestions,
)
from .sanctity import (  # noqa: F401
    build_sanctity_activity,
    extract_asset_types,
    extract_level,
    extract_signals,
    validate_relationship,
)
from .actions_persist import record_sanctity_flag  # noqa: F401
from .sanctity_evidence import validate_entity_relationship  # noqa: F401
from .triggers import (  # noqa: F401
    EXPIRY_DEFAULT_WINDOW_DAYS,
    build_external_input_entry,
    build_scheduled_entry,
    build_threshold_entry,
    scan_due_schedules,
    scan_expiries,
)

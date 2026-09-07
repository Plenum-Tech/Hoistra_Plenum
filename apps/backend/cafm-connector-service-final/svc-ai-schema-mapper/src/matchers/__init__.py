"""CMMS field matching strategies and utilities."""

from .cmms_aliases import CMMS_ALIASES, get_cmms_alias, get_reversed_aliases
from .regex_patterns import PATTERNS, match_field_by_pattern
from .dataset_describer import describe_dataset, match_tables_to_cafm
from .mapping_doc_parser import parse_mapping_doc
from .registry import registry_lookup, registry_lookup_learned_only, registry_append, registry_stats

__all__ = [
    "CMMS_ALIASES",
    "get_cmms_alias",
    "get_reversed_aliases",
    "PATTERNS",
    "match_field_by_pattern",
    "describe_dataset",
    "match_tables_to_cafm",
    "parse_mapping_doc",
    "registry_lookup",
    "registry_lookup_learned_only",
    "registry_append",
    "registry_stats",
]

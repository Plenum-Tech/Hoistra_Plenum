"""Platform schema connectors for svc-ai-schema-mapper.

Connectors fetch live schema from CMMS platforms and convert to mapper format.
"""

from .fiix_connector import FiixAPI, FiixError, FiixSchemaConnector

__all__ = [
    "FiixAPI",
    "FiixError",
    "FiixSchemaConnector",
]

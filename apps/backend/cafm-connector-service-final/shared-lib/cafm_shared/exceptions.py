"""
cafm_shared/exceptions.py

Re-exports the full CAFM exception hierarchy from cafm-connector-service.

All services (svc-ingestion, svc-query) import exceptions from here.
Never import directly from cafm_connector.core.exceptions in new services.

Note: ConnectionError here is the CAFM-specific subclass of ConnectorError,
not the Python builtin. Be aware of this when catching broad exception types.
"""

from __future__ import annotations

from cafm_connector.core.exceptions import (
    AuthenticationError,
    CAFMError,
    ConfigurationError,
    ConnectorAlreadyRegisteredError,
    ConnectorError,
    ConnectorNotFoundError,
    DataError,
    ImportError,
    JobAlreadyCancelledError,
    JobNotFoundError,
    MappingError,
    QueryError,
    SchemaDiscoveryError,
    SchemaError,
    SecretsError,
)
from cafm_connector.core.exceptions import ConnectionError as CAFMConnectionError

__all__ = [
    "CAFMError",
    "ConnectorError",
    "CAFMConnectionError",
    "AuthenticationError",
    "ConnectorNotFoundError",
    "ConnectorAlreadyRegisteredError",
    "SchemaError",
    "SchemaDiscoveryError",
    "DataError",
    "QueryError",
    "MappingError",
    "ImportError",
    "JobNotFoundError",
    "JobAlreadyCancelledError",
    "SecretsError",
    "ConfigurationError",
]

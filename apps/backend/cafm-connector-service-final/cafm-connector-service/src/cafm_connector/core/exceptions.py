"""Exception hierarchy for the CAFM connector service."""

from __future__ import annotations


class CAFMError(Exception):
    """Base for all CAFM errors."""


# ── Connector ──────────────────────────────────────────────────────────

class ConnectorError(CAFMError):
    """Base for connector errors."""

class ConnectionError(ConnectorError):
    """Could not establish or maintain a connection."""

class AuthenticationError(ConnectorError):
    """Bad credentials or permission denied."""

class ConnectorNotFoundError(ConnectorError):
    """No connector registered for the requested source type."""

class ConnectorAlreadyRegisteredError(ConnectorError):
    """A connector for this source type is already in the registry."""


# ── Schema ─────────────────────────────────────────────────────────────

class SchemaError(CAFMError):
    """Base for schema errors."""

class SchemaDiscoveryError(SchemaError):
    """Could not introspect the data source schema."""


# ── Data / Query ───────────────────────────────────────────────────────

class DataError(CAFMError):
    """Base for data access errors."""

class QueryError(DataError):
    """A query against the source failed."""

class MappingError(DataError):
    """Field mapping between source and target failed."""


# ── Import / Job ───────────────────────────────────────────────────────

class ImportError(CAFMError):
    """Base for import job errors."""

class JobNotFoundError(ImportError):
    """No job found with the given ID."""

class JobAlreadyCancelledError(ImportError):
    """Attempted to cancel a job that is already terminal."""


# ── Secrets ────────────────────────────────────────────────────────────

class SecretsError(CAFMError):
    """Could not read or write a secret."""


# ── Configuration ──────────────────────────────────────────────────────

class ConfigurationError(CAFMError):
    """Invalid or missing configuration."""

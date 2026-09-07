"""
Connector registry — maps DataSourceType → Connector class.

Auto-discovery via Python entry_points (pyproject.toml):
    [project.entry-points."cafm.connectors"]
    postgresql = "cafm_connector.connectors.plugins.postgresql:PostgreSQLConnector"
    ...

Third-party connectors can register the same way from their own packages.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig
from cafm_connector.core.exceptions import (
    ConnectorAlreadyRegisteredError,
    ConnectorNotFoundError,
)
from cafm_connector.core.types import DataSourceType

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """
    Singleton registry.

    Usage:
        registry = ConnectorRegistry()
        registry.discover_plugins()   # called once at startup

        connector = registry.create(config)
    """

    _instance: ClassVar[ConnectorRegistry | None] = None
    _plugins: dict[DataSourceType, type[Connector]]

    def __new__(cls) -> ConnectorRegistry:
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._plugins = {}
            cls._instance = inst
        return cls._instance

    # ── Registration ───────────────────────────────────────────────

    def register(
        self,
        connector_cls: type[Connector],
        *,
        allow_override: bool = False,
    ) -> None:
        """Manually register a connector class."""
        st = connector_cls.source_type
        if st in self._plugins and not allow_override:
            raise ConnectorAlreadyRegisteredError(
                f"Connector for '{st}' already registered. "
                "Use allow_override=True to replace it."
            )
        self._plugins[st] = connector_cls
        logger.info("connector_registered type=%s class=%s", st, connector_cls.__name__)

    def discover_plugins(self) -> None:
        """
        Auto-discover and register all connectors declared under
        the 'cafm.connectors' entry_points group.
        """
        eps = entry_points(group="cafm.connectors")
        for ep in eps:
            try:
                cls = ep.load()
                if isinstance(cls, type) and issubclass(cls, Connector):
                    self.register(cls, allow_override=True)
                else:
                    logger.warning("entry_point_not_a_connector name=%s", ep.name)
            except Exception:
                logger.exception("plugin_load_failed name=%s", ep.name)

    # ── Lookup ─────────────────────────────────────────────────────

    def get(self, source_type: DataSourceType) -> type[Connector]:
        """Return the class for a source type. Raises if not found."""
        try:
            return self._plugins[source_type]
        except KeyError:
            raise ConnectorNotFoundError(
                f"No connector registered for '{source_type}'. "
                f"Available: {self.list_registered()}"
            )

    def create(self, config: ConnectorConfig) -> Connector:
        """Instantiate a connector from a config object."""
        cls = self.get(config.source_type)
        return cls(config)

    def list_registered(self) -> list[str]:
        return [str(k) for k in self._plugins]

    def is_registered(self, source_type: DataSourceType) -> bool:
        return source_type in self._plugins

    # ── Testing helper ─────────────────────────────────────────────

    @classmethod
    def reset(cls) -> None:
        """Destroy singleton — use in tests only."""
        cls._instance = None

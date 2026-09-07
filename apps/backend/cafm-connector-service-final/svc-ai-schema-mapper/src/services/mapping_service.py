"""Service for retrieving and applying stored CMMS mapping templates.

Used during CSV/file ingest to:
1. Auto-detect which stored mapping applies
2. Load the mapping configuration
3. Apply it to deterministic_mapper
"""

from typing import Optional, Dict, Any
from uuid import UUID
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger
from ..models.migration import MappingTemplate

logger = get_logger(__name__)


class MappingService:
    """Service for mapping template operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def lookup_mapping(
        self,
        organization_id: UUID,
        source_system: str,
        table_name: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Look up the active mapping for a given organization, source system, and table.

        Used during ingest to auto-detect and load the appropriate mapping.

        Args:
            organization_id: Organization UUID
            source_system: Source system name (Maximo, Fiix, SAP PM, etc)
            table_name: Table name (assets, work_orders, parts, etc)

        Returns:
            Mapping configuration dict (config_json) or None if not found
        """
        try:
            query = (
                select(MappingTemplate)
                .where(
                    MappingTemplate.organization_id == organization_id,
                    MappingTemplate.source_system == source_system,
                    MappingTemplate.table_name == table_name,
                    MappingTemplate.is_active == True,
                )
                .order_by(MappingTemplate.version.desc())
                .limit(1)
            )

            result = await self.session.execute(query)
            mapping = result.scalars().first()

            if mapping:
                logger.info(
                    "mapping_loaded_from_db",
                    mapping_id=str(mapping.id),
                    source_system=source_system,
                    table_name=table_name,
                    version=mapping.version,
                )
                return mapping.config_json
            else:
                logger.debug(
                    "mapping_not_found",
                    organization_id=str(organization_id),
                    source_system=source_system,
                    table_name=table_name,
                )
                return None

        except Exception as e:
            logger.error(
                "mapping_lookup_error",
                error=str(e),
                organization_id=str(organization_id),
                source_system=source_system,
                table_name=table_name,
                exc_info=True,
            )
            return None

    async def list_mappings(
        self,
        organization_id: UUID,
        source_system: Optional[str] = None,
        table_name: Optional[str] = None,
        is_active: bool = True,
    ) -> list[Dict[str, Any]]:
        """
        List available mappings for an organization.

        Args:
            organization_id: Organization UUID
            source_system: Optional filter by source system
            table_name: Optional filter by table name
            is_active: Filter by active status (default: True)

        Returns:
            List of mapping configurations
        """
        try:
            query = select(MappingTemplate).where(
                MappingTemplate.organization_id == organization_id,
                MappingTemplate.is_active == is_active,
            )

            if source_system:
                query = query.where(MappingTemplate.source_system == source_system)

            if table_name:
                query = query.where(MappingTemplate.table_name == table_name)

            result = await self.session.execute(query)
            mappings = result.scalars().all()

            logger.info(
                "mappings_listed",
                organization_id=str(organization_id),
                count=len(mappings),
                source_system=source_system,
                table_name=table_name,
            )

            return [m.config_json for m in mappings]

        except Exception as e:
            logger.error(
                "mapping_list_error",
                error=str(e),
                organization_id=str(organization_id),
                exc_info=True,
            )
            return []

    async def get_mapping_by_id(
        self,
        mapping_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a mapping by its ID.

        Args:
            mapping_id: Mapping template UUID

        Returns:
            Mapping configuration dict or None if not found
        """
        try:
            query = select(MappingTemplate).where(MappingTemplate.id == mapping_id)
            result = await self.session.execute(query)
            mapping = result.scalars().first()

            if mapping:
                logger.info("mapping_retrieved_by_id", mapping_id=str(mapping_id))
                return mapping.config_json
            else:
                logger.debug("mapping_not_found_by_id", mapping_id=str(mapping_id))
                return None

        except Exception as e:
            logger.error(
                "mapping_retrieval_error",
                error=str(e),
                mapping_id=str(mapping_id),
                exc_info=True,
            )
            return None

    @staticmethod
    def validate_mapping_config(config: Dict[str, Any]) -> bool:
        """
        Validate a mapping configuration structure.

        Args:
            config: Mapping configuration to validate

        Returns:
            True if valid, False otherwise
        """
        if not isinstance(config, dict):
            logger.warning("invalid_mapping_config_not_dict")
            return False

        required_keys = ["canonical_fields", "vendor_aliases"]
        for key in required_keys:
            if key not in config:
                logger.warning("mapping_config_missing_key", key=key)
                return False

            if not isinstance(config[key], dict):
                logger.warning(
                    "mapping_config_key_not_dict",
                    key=key,
                    actual_type=type(config[key]).__name__,
                )
                return False

        return True

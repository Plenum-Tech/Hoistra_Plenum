"""API endpoints for managing stored CMMS mapping templates.

Endpoints:
  POST   /api/mappings — create/upload a new mapping
  GET    /api/mappings — list all mappings for an organization
  GET    /api/mappings/{mapping_id} — retrieve a specific mapping
  GET    /api/mappings/lookup/{source_system}/{table_name} — auto-lookup active mapping
  PUT    /api/mappings/{mapping_id} — update a mapping
  DELETE /api/mappings/{mapping_id} — soft delete a mapping
"""

from typing import Optional, Annotated
from uuid import UUID
from datetime import datetime
import json

from fastapi import APIRouter, HTTPException, Depends, Query, Path, Body
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger
from ..db import get_async_session_factory
from ..models.migration import MappingTemplate

logger = get_logger(__name__)

router = APIRouter(prefix="/api/mappings", tags=["mappings"])


# ── Dependency: get DB session ──
async def get_db_session() -> AsyncSession:
    """Dependency to inject AsyncSession."""
    factory = get_async_session_factory()
    async with factory() as session:
        yield session


# ── Request/Response Models ──
class MappingTemplateCreate(object):
    """Schema for creating a new mapping template."""

    def __init__(
        self,
        source_system: str,
        table_name: str,
        name: str,
        config_json: dict,
        organization_id: UUID,
        version: int = 1,
    ):
        self.source_system = source_system
        self.table_name = table_name
        self.name = name
        self.config_json = config_json
        self.organization_id = organization_id
        self.version = version


class MappingTemplateResponse(object):
    """Schema for returning a mapping template."""

    def __init__(self, mapping: MappingTemplate):
        self.id = str(mapping.id)
        self.organization_id = str(mapping.organization_id)
        self.source_system = mapping.source_system
        self.table_name = mapping.table_name
        self.name = mapping.name
        self.version = mapping.version
        self.config_json = mapping.config_json
        self.is_active = mapping.is_active
        self.created_by = str(mapping.created_by) if mapping.created_by else None
        self.created_at = mapping.created_at.isoformat()
        self.updated_at = mapping.updated_at.isoformat()


# ── POST /api/mappings — Create a new mapping ──
@router.post("/", response_model=dict)
async def create_mapping(
    source_system: str = Query(..., description="Source system name (Maximo, Fiix, SAP PM, etc)"),
    table_name: str = Query(..., description="Table name (assets, work_orders, parts, etc)"),
    name: str = Query(..., description="Human-readable mapping name"),
    organization_id: UUID = Query(..., description="Organization ID"),
    config_json: dict = Body(..., description="Full mapping configuration as JSON"),
    version: int = Query(1, description="Mapping version"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Create a new mapping template in the database.

    **Parameters:**
    - `source_system`: Source CMMS system (e.g., "Maximo", "Fiix", "SAP PM", "Archibus")
    - `table_name`: Target table name (e.g., "assets", "work_orders", "parts")
    - `name`: Human-readable name for this mapping
    - `organization_id`: Organization UUID
    - `config_json`: Full mapping configuration including canonical_fields, vendor_aliases, regex_patterns, confidence_overrides
    - `version`: Version number (default 1)

    **Response:** Mapping template object with ID and metadata
    """
    try:
        # Accept any valid JSON - JSONB is flexible
        if not isinstance(config_json, dict):
            raise HTTPException(status_code=400, detail="config_json must be a dictionary")

        # Create mapping
        new_mapping = MappingTemplate(
            source_system=source_system,
            table_name=table_name,
            name=name,
            organization_id=organization_id,
            config_json=config_json,
            version=version,
            is_active=True,
        )

        session.add(new_mapping)
        await session.commit()
        await session.refresh(new_mapping)

        logger.info(
            "mapping_created",
            mapping_id=str(new_mapping.id),
            source_system=source_system,
            table_name=table_name,
            organization_id=str(organization_id),
        )

        return MappingTemplateResponse(new_mapping).__dict__

    except HTTPException:
        raise
    except Exception as e:
        logger.error("mapping_creation_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create mapping: {str(e)}")


# ── GET /api/mappings — List all mappings ──
@router.get("/", response_model=dict)
async def list_mappings(
    organization_id: UUID = Query(..., description="Organization ID"),
    source_system: Optional[str] = Query(None, description="Filter by source system"),
    table_name: Optional[str] = Query(None, description="Filter by table name"),
    is_active: Optional[bool] = Query(True, description="Filter by active status"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    List all mapping templates for an organization.

    **Parameters:**
    - `organization_id`: Organization UUID (required)
    - `source_system`: Optional filter by source system
    - `table_name`: Optional filter by table name
    - `is_active`: Filter by active status (default: True — only active)

    **Response:** List of mapping templates
    """
    try:
        query = select(MappingTemplate).where(
            MappingTemplate.organization_id == organization_id
        )

        if source_system:
            query = query.where(MappingTemplate.source_system == source_system)

        if table_name:
            query = query.where(MappingTemplate.table_name == table_name)

        if is_active is not None:
            query = query.where(MappingTemplate.is_active == is_active)

        result = await session.execute(query)
        mappings = result.scalars().all()

        logger.info(
            "mappings_listed",
            organization_id=str(organization_id),
            count=len(mappings),
        )

        return {
            "mappings": [MappingTemplateResponse(m).__dict__ for m in mappings],
            "total": len(mappings),
        }

    except Exception as e:
        logger.error("mappings_list_failed", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list mappings: {str(e)}")


# ── GET /api/mappings/{mapping_id} — Retrieve a specific mapping ──
@router.get("/{mapping_id}", response_model=dict)
async def get_mapping(
    mapping_id: UUID = Path(..., description="Mapping template ID"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Retrieve a specific mapping template by ID.

    **Parameters:**
    - `mapping_id`: Mapping template UUID

    **Response:** Mapping template object with full configuration
    """
    try:
        query = select(MappingTemplate).where(MappingTemplate.id == mapping_id)
        result = await session.execute(query)
        mapping = result.scalars().first()

        if not mapping:
            raise HTTPException(status_code=404, detail=f"Mapping {mapping_id} not found")

        logger.info("mapping_retrieved", mapping_id=str(mapping_id))
        return MappingTemplateResponse(mapping).__dict__

    except HTTPException:
        raise
    except Exception as e:
        logger.error("mapping_retrieval_failed", mapping_id=str(mapping_id), error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to retrieve mapping: {str(e)}")


# ── GET /api/mappings/lookup/{source_system}/{table_name} — Auto-lookup ──
@router.get("/lookup/{source_system}/{table_name}", response_model=dict)
async def lookup_mapping(
    source_system: str = Path(..., description="Source system name"),
    table_name: str = Path(..., description="Table name"),
    organization_id: UUID = Query(..., description="Organization ID"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Auto-lookup the active mapping for a given source system and table.

    This is called during CSV/file ingest to automatically find the
    appropriate stored mapping configuration.

    **Parameters:**
    - `source_system`: Source system name (e.g., "Maximo", "Fiix")
    - `table_name`: Table name (e.g., "assets", "work_orders")
    - `organization_id`: Organization UUID

    **Response:** Active mapping template or 404 if not found
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

        result = await session.execute(query)
        mapping = result.scalars().first()

        if not mapping:
            logger.warning(
                "mapping_not_found_lookup",
                organization_id=str(organization_id),
                source_system=source_system,
                table_name=table_name,
            )
            raise HTTPException(
                status_code=404,
                detail=f"No active mapping found for {source_system}/{table_name}",
            )

        logger.info(
            "mapping_lookup_success",
            source_system=source_system,
            table_name=table_name,
            mapping_id=str(mapping.id),
        )
        return MappingTemplateResponse(mapping).__dict__

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "mapping_lookup_failed",
            source_system=source_system,
            table_name=table_name,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=f"Lookup failed: {str(e)}")


# ── PUT /api/mappings/{mapping_id} — Update a mapping ──
@router.put("/{mapping_id}", response_model=dict)
async def update_mapping(
    mapping_id: UUID = Path(..., description="Mapping template ID"),
    name: Optional[str] = Query(None, description="New name for the mapping"),
    config_json: Optional[dict] = Body(None, description="Updated mapping configuration"),
    is_active: Optional[bool] = Query(None, description="Active status"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Update a mapping template.

    **Parameters:**
    - `mapping_id`: Mapping template UUID
    - `name`: Optional new name
    - `config_json`: Optional updated configuration
    - `is_active`: Optional new active status

    **Response:** Updated mapping template
    """
    try:
        query = select(MappingTemplate).where(MappingTemplate.id == mapping_id)
        result = await session.execute(query)
        mapping = result.scalars().first()

        if not mapping:
            raise HTTPException(status_code=404, detail=f"Mapping {mapping_id} not found")

        # Update fields
        if name is not None:
            mapping.name = name

        if config_json is not None:
            # Validate structure
            required_fields = ["canonical_fields", "vendor_aliases"]
            missing = [f for f in required_fields if f not in config_json]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"config_json missing required fields: {missing}",
                )
            mapping.config_json = config_json

        if is_active is not None:
            mapping.is_active = is_active

        await session.commit()
        await session.refresh(mapping)

        logger.info("mapping_updated", mapping_id=str(mapping_id))
        return MappingTemplateResponse(mapping).__dict__

    except HTTPException:
        raise
    except Exception as e:
        logger.error("mapping_update_failed", mapping_id=str(mapping_id), error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to update mapping: {str(e)}")


# ── DELETE /api/mappings/{mapping_id} — Soft delete ──
@router.delete("/{mapping_id}", status_code=204)
async def delete_mapping(
    mapping_id: UUID = Path(..., description="Mapping template ID"),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """
    Soft-delete a mapping template by setting is_active = False.

    The record is retained in the database for audit purposes.

    **Parameters:**
    - `mapping_id`: Mapping template UUID
    """
    try:
        query = select(MappingTemplate).where(MappingTemplate.id == mapping_id)
        result = await session.execute(query)
        mapping = result.scalars().first()

        if not mapping:
            raise HTTPException(status_code=404, detail=f"Mapping {mapping_id} not found")

        mapping.is_active = False
        await session.commit()

        logger.info("mapping_deleted", mapping_id=str(mapping_id))

    except HTTPException:
        raise
    except Exception as e:
        logger.error("mapping_deletion_failed", mapping_id=str(mapping_id), error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to delete mapping: {str(e)}")

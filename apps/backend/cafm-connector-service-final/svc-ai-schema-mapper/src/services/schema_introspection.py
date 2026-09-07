"""Schema introspection service for plenum_cafm database.

Dynamically generates canonical fields mapping and vendor aliases from the actual
database structure instead of relying on hardcoded field definitions.

Usage:
    service = SchemaIntrospectionService(db_url)
    mapper_config = await service.build_default_mapper_config()
    # Returns: {
    #     "source_system": "plenum_cafm",
    #     "canonical_fields": {field_name: description, ...},
    #     "vendor_aliases": {canonical_field: [alias1, alias2, ...], ...},
    #     ...other mapper fields...
    # }
"""

import logging
from typing import Dict, List, Any, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

SCHEMA_NAME = "plenum_cafm"

# Field name patterns for generating descriptions
FIELD_PATTERNS = {
    "code": "Unique identifier or code",
    "id": "Primary or foreign key identifier",
    "name": "Human-readable name or title",
    "description": "Detailed description or notes",
    "status": "Current status or state",
    "priority": "Priority level or ranking",
    "type": "Type or category classification",
    "date": "Date value",
    "timestamp": "Date and time value",
    "count": "Numeric count or quantity",
    "value": "Numeric value or measurement",
    "amount": "Monetary or numeric amount",
    "percentage": "Percentage value",
    "score": "Score or rating value",
    "url": "Web address or file URL",
    "email": "Email address",
    "phone": "Phone number",
    "address": "Physical address",
    "location": "Location or site reference",
    "serial": "Serial number",
    "manufacturer": "Equipment manufacturer",
    "model": "Equipment model",
    "at": "Timestamp or datetime",
}


class SchemaIntrospectionService:
    """Service for introspecting plenum_cafm database schema and generating mapper config."""

    def __init__(self, db_url: str):
        """
        Initialize schema introspection service.

        Args:
            db_url: Async database URL (e.g., postgresql+asyncpg://...)
        """
        self.db_url = db_url
        self.engine = None

    async def build_default_mapper_config(self) -> Dict[str, Any]:
        """
        Build a complete JsonMapperConfig-compatible dict from the live DB schema.

        Returns:
            Dictionary with structure:
            {
                "version": "1.0",
                "source_system": "plenum_cafm",
                "canonical_fields": {col_name: description, ...},
                "vendor_aliases": {canonical_field: [alias1, alias2, ...], ...},
            }
        """
        try:
            await self._initialize_engine()

            # Step 1: Get all columns from plenum_cafm schema
            canonical_fields = await self._get_canonical_fields()
            logger.info(f"Generated {len(canonical_fields)} canonical fields from {SCHEMA_NAME} schema")

            # Step 2: Build vendor aliases by reversing CMMS_ALIASES
            vendor_aliases = await self._build_vendor_aliases(list(canonical_fields.keys()))
            logger.info(f"Generated vendor aliases for {len(vendor_aliases)} canonical fields")

            # Step 3: Assemble complete mapper config
            mapper_config = {
                "version": "1.0",
                "source_system": SCHEMA_NAME,
                "canonical_fields": canonical_fields,
                "vendor_aliases": vendor_aliases,
            }

            return mapper_config

        except Exception as e:
            logger.error(f"Failed to build mapper config from schema: {e}")
            raise
        finally:
            if self.engine:
                await self.engine.dispose()

    async def _initialize_engine(self):
        """Initialize database connection with NullPool."""
        from ..db import _async_engine_connect_args, _strip_sslmode_from_async_url

        self.engine = create_async_engine(
            _strip_sslmode_from_async_url(self.db_url),
            echo=False,
            poolclass=NullPool,
            connect_args=_async_engine_connect_args(self.db_url),
        )
        logger.debug("Schema introspection engine initialized")

    async def _get_canonical_fields(self) -> Dict[str, str]:
        """
        Extract all column names from plenum_cafm schema and generate descriptions.

        Returns:
            Dictionary mapping column names to descriptions.
            Example:
                {
                    "asset_code": "Unique identifier or code in assets table",
                    "asset_name": "Human-readable name or title in assets table",
                    ...
                }
        """
        canonical_fields = {}

        async with self.engine.connect() as conn:
            # Get all table names in plenum_cafm schema
            result = await conn.execute(
                text("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = :schema_name
                    AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """),
                {"schema_name": SCHEMA_NAME}
            )

            tables = [row[0] for row in result.fetchall()]
            logger.debug(f"Found {len(tables)} tables in {SCHEMA_NAME} schema")

            # For each table, get all columns
            for table_name in tables:
                result = await conn.execute(
                    text("""
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = :schema_name
                        AND table_name = :table_name
                        ORDER BY ordinal_position
                    """),
                    {
                        "schema_name": SCHEMA_NAME,
                        "table_name": table_name,
                    }
                )

                columns = result.fetchall()

                # Generate canonical field name and description for each column
                for col_name, col_type in columns:
                    # Use column name as-is (keep exact DB naming)
                    description = self._generate_description(col_name, col_type, table_name)

                    # Store in canonical_fields
                    # Keep lowercase normalized version as key
                    normalized_key = col_name.lower()

                    # Avoid duplicates across tables - if exists, append table context
                    if normalized_key in canonical_fields:
                        existing = canonical_fields[normalized_key]
                        if table_name not in existing:
                            canonical_fields[normalized_key] = f"{existing} (also in {table_name})"
                    else:
                        canonical_fields[normalized_key] = description

        return canonical_fields

    def _generate_description(self, col_name: str, col_type: str, table_name: str) -> str:
        """
        Generate human-readable description for a column based on name and type.

        Args:
            col_name: Column name
            col_type: SQL data type
            table_name: Table name (for context)

        Returns:
            Human-readable description
        """
        col_lower = col_name.lower()

        # Find matching pattern in FIELD_PATTERNS
        pattern_match = None
        for pattern, description in FIELD_PATTERNS.items():
            if pattern in col_lower:
                pattern_match = description
                break

        # Default description if no pattern matches
        if not pattern_match:
            pattern_match = "Data field"

        # Add type-specific info
        if "timestamp" in col_type.lower() or "datetime" in col_type.lower():
            description = f"Timestamp or datetime value in {table_name}"
        elif "uuid" in col_type.lower():
            description = f"Unique identifier (UUID) in {table_name}"
        elif "numeric" in col_type.lower() or "decimal" in col_type.lower():
            description = f"Numeric value in {table_name}"
        elif "boolean" in col_type.lower() or "bool" in col_type.lower():
            description = f"Boolean flag or indicator in {table_name}"
        elif "text" in col_type.lower():
            description = f"Text content in {table_name}"
        elif "json" in col_type.lower():
            description = f"JSON data in {table_name}"
        else:
            # Use pattern match with table context
            description = f"{pattern_match} in {table_name}"

        return description

    async def _build_vendor_aliases(self, canonical_field_names: List[str]) -> Dict[str, List[str]]:
        """
        Build vendor aliases mapping from existing CMMS_ALIASES.

        Reverses CMMS_ALIASES so that each canonical field maps to all its known aliases.
        Also adds auto-generated case/format variations.

        Args:
            canonical_field_names: List of canonical field names from DB

        Returns:
            Dictionary: {canonical_field: [alias1, alias2, ...], ...}
        """
        # Import here to avoid circular imports
        from ..matchers.cmms_aliases import CMMS_ALIASES

        vendor_aliases: Dict[str, List[str]] = {}

        # Step 1: Reverse CMMS_ALIASES
        for alias, canonical in CMMS_ALIASES.items():
            if canonical not in vendor_aliases:
                vendor_aliases[canonical] = []
            if alias not in vendor_aliases[canonical]:
                vendor_aliases[canonical].append(alias)

        # Step 2: Ensure all canonical fields exist in vendor_aliases (even if no aliases found)
        for canonical_field in canonical_field_names:
            if canonical_field not in vendor_aliases:
                vendor_aliases[canonical_field] = []

        # Step 3: Add auto-generated variations for each canonical field
        for canonical_field in canonical_field_names:
            # Add the canonical field itself
            if canonical_field not in vendor_aliases[canonical_field]:
                vendor_aliases[canonical_field].append(canonical_field)

            # CamelCase and PascalCase variations
            camel = self._to_camel_case(canonical_field)
            pascal = self._to_pascal_case(canonical_field)

            for variant in [camel, pascal, canonical_field.upper(), canonical_field.upper().replace("_", "")]:
                if variant not in vendor_aliases[canonical_field]:
                    vendor_aliases[canonical_field].append(variant)

        return vendor_aliases

    @staticmethod
    def _to_camel_case(snake_str: str) -> str:
        """Convert snake_case to camelCase."""
        components = snake_str.split("_")
        return components[0] + "".join(x.title() for x in components[1:])

    @staticmethod
    def _to_pascal_case(snake_str: str) -> str:
        """Convert snake_case to PascalCase."""
        components = snake_str.split("_")
        return "".join(x.title() for x in components)

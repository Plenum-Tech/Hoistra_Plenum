#!/usr/bin/env python
"""
Quick test to verify SchemaIntrospectionService works correctly.
Tests dynamic schema introspection from DB.
"""

import asyncio
import json
import os
from dotenv import load_dotenv

async def test_schema_introspection():
    """Test SchemaIntrospectionService."""
    load_dotenv()

    db_url = os.getenv("DB_URL")
    if not db_url:
        print("[ERROR] DB_URL not set in environment")
        return False

    print(f"[INFO] Testing SchemaIntrospectionService with DB: {db_url.split('@')[1] if '@' in db_url else 'unknown'}")

    try:
        from src.services.schema_introspection import SchemaIntrospectionService

        service = SchemaIntrospectionService(db_url)

        print("[INFO] Building default mapper config from plenum_cafm schema...")
        mapper_config = await service.build_default_mapper_config()

        # Verify structure
        assert "version" in mapper_config, "Missing version"
        assert "source_system" in mapper_config, "Missing source_system"
        assert "canonical_fields" in mapper_config, "Missing canonical_fields"
        assert "vendor_aliases" in mapper_config, "Missing vendor_aliases"

        # Verify content
        canonical_fields = mapper_config["canonical_fields"]
        vendor_aliases = mapper_config["vendor_aliases"]

        print(f"[OK] Generated {len(canonical_fields)} canonical fields")
        print(f"[OK] Generated {len(vendor_aliases)} vendor alias groups")
        print(f"[OK] Source system: {mapper_config['source_system']}")

        # Show sample fields
        print("\n[INFO] Sample canonical fields (first 10):")
        for i, (field, desc) in enumerate(list(canonical_fields.items())[:10]):
            print(f"   {field}: {desc[:60]}...")

        # Show sample aliases
        print("\n[INFO] Sample vendor aliases (first 5 groups):")
        for canonical, aliases in list(vendor_aliases.items())[:5]:
            print(f"   {canonical}: {aliases[:3]}...")

        # Verify JsonMapperConfig compatibility
        from src.schemas import JsonMapperConfig

        try:
            config = JsonMapperConfig(**mapper_config)
            print(f"\n[OK] Mapper config is valid JsonMapperConfig")
            print(f"   Version: {config.version}")
            print(f"   Source: {config.source_system}")
            print(f"   Fields: {len(config.canonical_fields)}")
            print(f"   Aliases: {len(config.vendor_aliases)}")
        except Exception as e:
            print(f"\n[ERROR] JsonMapperConfig validation failed: {e}")
            return False

        print("\n[OK] Schema introspection test PASSED")
        return True

    except Exception as e:
        print(f"[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Run tests."""
    success = await test_schema_introspection()
    exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())

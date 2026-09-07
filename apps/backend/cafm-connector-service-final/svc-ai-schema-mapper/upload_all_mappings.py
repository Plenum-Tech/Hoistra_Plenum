#!/usr/bin/env python3
"""Upload all 37 CMMS mapping files to the PostgreSQL mapping_templates table.

Usage:
    python upload_all_mappings.py /path/to/mapping/files
"""

import json
import sys
import requests
import time
from pathlib import Path
from uuid import UUID
from typing import Optional

# Configuration
BASE_URL = "http://localhost:8003"
ORG_ID = UUID("00000000-0000-0000-0000-000000000001")  # Your org ID

# Color codes for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_success(msg: str):
    print(f"{GREEN}✅{RESET} {msg}")


def print_error(msg: str):
    print(f"{RED}❌{RESET} {msg}")


def print_info(msg: str):
    print(f"{BLUE}ℹ️{RESET} {msg}")


def print_warning(msg: str):
    print(f"{YELLOW}⚠️{RESET} {msg}")


def upload_mapping(mapping_file: Path, retry_count: int = 3) -> bool:
    """Upload a single mapping file with retry logic."""
    try:
        # Load JSON
        with open(mapping_file, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Extract metadata
        source_system = config.get("source_system", "Custom").strip()
        description = config.get("description", "")

        # Infer table_name from filename
        # Examples: assets_mapping.json → assets, work_orders_mapping.json → work_orders
        table_name = mapping_file.stem
        if table_name.endswith("_mapping"):
            table_name = table_name[:-8]  # Remove '_mapping' suffix
        table_name = table_name.replace("_", "_")

        # Prepare request
        params = {
            "source_system": source_system,
            "table_name": table_name,
            "name": f"{source_system} {table_name} mapping",
            "organization_id": str(ORG_ID),
        }

        payload = {"config_json": config}

        # Attempt upload with retries
        for attempt in range(1, retry_count + 1):
            try:
                response = requests.post(
                    f"{BASE_URL}/api/mappings",
                    params=params,
                    json=payload,
                    timeout=30,
                )

                if response.status_code == 200:
                    result = response.json()
                    mapping_id = result.get("id", "unknown")
                    print_success(
                        f"{mapping_file.name:40} → {source_system:12} / {table_name:20} (ID: {mapping_id[:8]}...)"
                    )
                    return True

                elif response.status_code == 400:
                    print_error(
                        f"{mapping_file.name:40} → Validation error: {response.text[:80]}"
                    )
                    return False

                else:
                    if attempt < retry_count:
                        print_warning(
                            f"{mapping_file.name:40} → Attempt {attempt}/{retry_count} failed (HTTP {response.status_code}), retrying..."
                        )
                        time.sleep(1)
                    else:
                        print_error(
                            f"{mapping_file.name:40} → Failed after {retry_count} attempts (HTTP {response.status_code})"
                        )
                        return False

            except requests.exceptions.Timeout:
                if attempt < retry_count:
                    print_warning(
                        f"{mapping_file.name:40} → Timeout on attempt {attempt}/{retry_count}, retrying..."
                    )
                    time.sleep(1)
                else:
                    print_error(
                        f"{mapping_file.name:40} → Timeout after {retry_count} attempts"
                    )
                    return False

            except requests.exceptions.ConnectionError:
                print_error(
                    f"{mapping_file.name:40} → Connection failed. Is the service running at {BASE_URL}?"
                )
                return False

    except json.JSONDecodeError:
        print_error(f"{mapping_file.name:40} → Invalid JSON format")
        return False

    except FileNotFoundError:
        print_error(f"{mapping_file.name:40} → File not found")
        return False

    except Exception as e:
        print_error(f"{mapping_file.name:40} → {type(e).__name__}: {str(e)[:60]}")
        return False

    return False


def main():
    """Main entry point."""
    print(f"\n{BLUE}{'='*100}{RESET}")
    print(f"{BLUE}📦 CMMS Mapping Uploader{RESET}")
    print(f"{BLUE}{'='*100}{RESET}\n")

    # Determine mapping directory
    if len(sys.argv) > 1:
        mappings_dir = Path(sys.argv[1])
    else:
        # Try common locations
        candidates = [
            Path.cwd() / "mappings",
            Path.cwd() / "mapping_files",
            Path.home() / "Downloads" / "mappings",
            Path("c:/Users/Lenovo/Downloads/mappings"),
        ]
        mappings_dir = None
        for candidate in candidates:
            if candidate.exists():
                mappings_dir = candidate
                break

        if not mappings_dir:
            print_error("Could not find mappings directory.")
            print(f"Usage: python {sys.argv[0]} /path/to/mapping/files")
            sys.exit(1)

    # Verify directory
    if not mappings_dir.exists():
        print_error(f"Directory not found: {mappings_dir}")
        sys.exit(1)

    # Find all JSON mapping files
    mapping_files = sorted(mappings_dir.glob("*.json"))
    if not mapping_files:
        print_error(f"No JSON mapping files found in: {mappings_dir}")
        sys.exit(1)

    print_info(f"Mapping directory: {mappings_dir}")
    print_info(f"Found {len(mapping_files)} mapping files")
    print_info(f"Target: {BASE_URL}")
    print_info(f"Organization: {ORG_ID}\n")

    # Check service availability
    print_info("Checking service availability...")
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            print_success("Service is running ✓")
        else:
            print_warning(f"Service returned HTTP {response.status_code}")
    except requests.exceptions.ConnectionError:
        print_error(
            f"Cannot connect to {BASE_URL}. Is the service running?"
        )
        print("Start it with: docker-compose up")
        sys.exit(1)
    except Exception as e:
        print_warning(f"Health check warning: {str(e)}")

    print("\n" + "=" * 100 + "\n")

    # Upload all mappings
    success_count = 0
    fail_count = 0
    start_time = time.time()

    for i, mapping_file in enumerate(mapping_files, 1):
        prefix = f"[{i:2d}/{len(mapping_files):2d}]"
        if upload_mapping(mapping_file):
            success_count += 1
        else:
            fail_count += 1

    # Summary
    elapsed = time.time() - start_time
    print("\n" + "=" * 100)
    print(f"\n{BLUE}📊 Upload Summary{RESET}")
    print(f"  {GREEN}✅ Successful:{RESET} {success_count}/{len(mapping_files)}")
    if fail_count > 0:
        print(f"  {RED}❌ Failed:{RESET} {fail_count}/{len(mapping_files)}")
    print(f"  ⏱️  Time elapsed: {elapsed:.1f}s")
    print()

    if success_count == len(mapping_files):
        print_success(f"All {len(mapping_files)} mappings uploaded successfully!")
        print()
        print_info("Next steps:")
        print(f"  1. Verify mappings: curl {BASE_URL}/api/mappings?organization_id={ORG_ID}")
        print(f"  2. Test with CSV: curl -F 'file=@test.csv' {BASE_URL}/api/migration/start")
        print()
        return 0
    else:
        print_error(f"{fail_count} mappings failed to upload")
        print()
        print_info("Troubleshooting:")
        print("  • Check service logs: docker-compose logs -f svc-ai-schema-mapper")
        print("  • Verify mapping JSON format is valid")
        print("  • Check organization_id is correct")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())

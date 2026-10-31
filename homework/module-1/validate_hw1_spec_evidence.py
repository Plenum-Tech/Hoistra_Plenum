#!/usr/bin/env python3
"""Validate Homework 1 expected outcomes against the canonical specification."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
POINTER_PATH = ROOT / "SPEC.md"
SPEC_PATH = ROOT / "specifications.md"
SESSION_PATH = ROOT / "homework/module-1/hw1-session.jsonl"
MAPPING_PATH = ROOT / "homework/module-1/hw1-case-spec-mapping.json"
READINESS_PATH = ROOT / "homework/module-1/readiness-requirement-status-v0.37.0.json"


def sha256_bytes(value: bytes) -> str:
    """Return a stable digest so evidence drift cannot pass unnoticed."""

    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read {path.relative_to(ROOT)}: {exc}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise ValueError(f"{path.relative_to(ROOT)}:{line_number} is blank")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{path.relative_to(ROOT)}:{line_number} is invalid JSON: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.relative_to(ROOT)}:{line_number} must be an object")
        rows.append(row)
    return rows


def validate() -> list[str]:
    errors: list[str] = []

    pointer_text = POINTER_PATH.read_text(encoding="utf-8")
    if "[specifications.md](specifications.md)" not in pointer_text:
        errors.append("SPEC.md must link directly to canonical specifications.md")
    if "hw1-expected-outcome-workflow.md" not in pointer_text:
        errors.append("SPEC.md must link to the mandatory expected-outcome workflow")

    spec_bytes = SPEC_PATH.read_bytes()
    spec_text = spec_bytes.decode("utf-8")
    spec_hash = sha256_bytes(spec_bytes)
    version_match = re.search(r"Specification version: `([^`]+)`", spec_text)
    if version_match is None:
        errors.append("specifications.md has no parseable specification version")
        spec_version = ""
    else:
        spec_version = version_match.group(1)

    readiness = load_json(READINESS_PATH)
    readiness_requirements = readiness.get("requirements")
    if not isinstance(readiness_requirements, list):
        errors.append("readiness requirement register has no requirements list")
        readiness_requirements = []
    requirement_ids = {
        item.get("id")
        for item in readiness_requirements
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if len(requirement_ids) != len(readiness_requirements):
        errors.append("readiness requirement IDs are missing or duplicated")
    if readiness.get("requirement_count") != len(requirement_ids):
        errors.append("readiness requirement_count does not match its unique IDs")
    if readiness.get("specification_sha256") != spec_hash:
        errors.append("readiness register does not match the canonical specification hash")
    if readiness.get("specification_version") != spec_version:
        errors.append("readiness register does not match the canonical specification version")
    # The hash-bound readiness register also indexes incorporated external contract
    # clauses and section aliases, so not every machine ID is literal spec text.

    checked_receipts: dict[str, str] = {}
    for requirement in readiness_requirements:
        if not isinstance(requirement, dict):
            continue
        for evidence in requirement.get("evidence") or []:
            if not isinstance(evidence, dict):
                errors.append(f"requirement {requirement.get('id')} has malformed evidence")
                continue
            receipt = evidence.get("receipt")
            expected_hash = evidence.get("sha256")
            if not isinstance(receipt, str) or not isinstance(expected_hash, str):
                errors.append(f"requirement {requirement.get('id')} has incomplete evidence")
                continue
            previous_hash = checked_receipts.get(receipt)
            if previous_hash is not None and previous_hash != expected_hash:
                errors.append(f"readiness register has conflicting hashes for {receipt}")
                continue
            checked_receipts[receipt] = expected_hash
    for receipt, expected_hash in sorted(checked_receipts.items()):
        receipt_path = ROOT / receipt
        if not receipt_path.is_file():
            errors.append(f"readiness evidence is missing: {receipt}")
        elif sha256_bytes(receipt_path.read_bytes()) != expected_hash:
            errors.append(f"readiness evidence hash does not match: {receipt}")
    activation_receipt = readiness.get("activation_receipt")
    if not isinstance(activation_receipt, str) or not (ROOT / activation_receipt).is_file():
        errors.append("readiness activation receipt is missing")

    mapping = load_json(MAPPING_PATH)
    mapping_spec = mapping.get("specification", {})
    if not isinstance(mapping_spec, dict):
        mapping_spec = {}
        errors.append("case mapping specification metadata must be an object")
    expected_spec_metadata = {
        "pointer": "SPEC.md",
        "canonical_path": "specifications.md",
        "version": spec_version,
        "sha256": spec_hash,
    }
    for key, expected_value in expected_spec_metadata.items():
        if mapping_spec.get(key) != expected_value:
            errors.append(f"case mapping specification.{key} is out of date")

    rows = load_jsonl(SESSION_PATH)
    source_log = mapping.get("source_log", {})
    if not isinstance(source_log, dict):
        source_log = {}
        errors.append("case mapping source_log must be an object")
    if source_log.get("path") != str(SESSION_PATH.relative_to(ROOT)):
        errors.append("case mapping source_log.path is incorrect")
    if source_log.get("sha256") != sha256_bytes(SESSION_PATH.read_bytes()):
        errors.append("case mapping source_log.sha256 does not match the JSONL")
    if source_log.get("rows") != len(rows):
        errors.append("case mapping source_log.rows does not match the JSONL")
    conversation_ids = {
        row.get("conversation_id") or row.get("session_id")
        for row in rows
        if row.get("conversation_id") or row.get("session_id")
    }
    if source_log.get("distinct_conversations") != len(conversation_ids):
        errors.append("case mapping distinct_conversations is incorrect")
    authenticated_conversation_ids = {
        row.get("conversation_id") or row.get("session_id")
        for row in rows
        if row.get("role") and (row.get("conversation_id") or row.get("session_id"))
    }
    if source_log.get("authenticated_conversations") != len(authenticated_conversation_ids):
        errors.append("case mapping authenticated_conversations is incorrect")
    if len(authenticated_conversation_ids) < 10:
        errors.append("Homework 1 requires at least 10 distinct authenticated conversations")

    cases = mapping.get("cases")
    if not isinstance(cases, list):
        errors.append("case mapping cases must be a list")
        cases = []
    if len(cases) != len(rows):
        errors.append("every JSONL row must have exactly one case mapping")

    case_ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    if len(case_ids) != len(set(case_ids)):
        errors.append("case mapping case_id values must be unique")
    mapped_rows = [case.get("row") for case in cases if isinstance(case, dict)]
    if sorted(row for row in mapped_rows if isinstance(row, int)) != list(range(1, len(rows) + 1)):
        errors.append("case mapping rows must cover each JSONL row once")

    cases_by_row = {
        case.get("row"): case
        for case in cases
        if isinstance(case, dict) and isinstance(case.get("row"), int)
    }
    for row_number, source in enumerate(rows, start=1):
        case = cases_by_row.get(row_number)
        if case is None:
            continue
        label = f"row {row_number} ({source.get('case_id', 'missing case_id')})"
        if case.get("case_id") != source.get("case_id"):
            errors.append(f"{label}: mapping case_id does not match JSONL")
        if case.get("request") != source.get("request"):
            errors.append(f"{label}: mapped request does not exactly match JSONL")
        if case.get("source_request_sha256") != sha256_text(str(source.get("request", ""))):
            errors.append(f"{label}: source_request_sha256 is missing or incorrect")
        if case.get("source_expected_sha256") != sha256_text(str(source.get("expected", ""))):
            errors.append(f"{label}: source_expected_sha256 is missing or incorrect")
        if case.get("primary_requirement") != source.get("requirement"):
            errors.append(f"{label}: primary requirement does not match JSONL")

        mapped_requirements = {case.get("primary_requirement")}
        related = case.get("related_requirements", [])
        if not isinstance(related, list):
            errors.append(f"{label}: related_requirements must be a list")
            related = []
        mapped_requirements.update(related)
        source_requirements = set(source.get("requirements") or [])
        if source.get("requirement"):
            source_requirements.add(source["requirement"])
        omitted = source_requirements - mapped_requirements
        if omitted:
            errors.append(f"{label}: mapping omits source requirements {sorted(omitted)}")
        unknown = mapped_requirements - requirement_ids
        if unknown:
            errors.append(f"{label}: mapping cites unknown requirements {sorted(unknown)}")

        if source.get("trace_id"):
            if case.get("trace_id") != source.get("trace_id"):
                errors.append(f"{label}: trace_id does not match JSONL")
        elif case.get("session_id") != source.get("session_id"):
            errors.append(f"{label}: session_id does not match JSONL")
        if case.get("met_requirement") != source.get("met_requirement"):
            errors.append(f"{label}: met_requirement does not match observed JSONL evidence")
        if not isinstance(case.get("expected"), str) or not case["expected"].strip():
            errors.append(f"{label}: human-readable expected outcome is required")

    valid_rows = set(range(1, len(rows) + 1))
    for case in cases:
        if not isinstance(case, dict):
            errors.append("every case mapping entry must be an object")
            continue
        for key in ("supersedes_row", "superseded_by_row"):
            target = case.get(key)
            if target is not None and target not in valid_rows:
                errors.append(f"case {case.get('case_id')}: {key} targets an unknown row")
        target_case = case.get("supersedes_case")
        if target_case is not None and target_case not in case_ids:
            errors.append(f"case {case.get('case_id')}: supersedes_case is unknown")

    part_c = mapping.get("part_c", {})
    if not isinstance(part_c, dict):
        part_c = {}
        errors.append("part_c mapping must be an object")
    unknown_part_c = set(part_c.get("requirement_ids") or []) - requirement_ids
    if unknown_part_c:
        errors.append(f"part_c cites unknown requirements {sorted(unknown_part_c)}")
    by_case = {case.get("case_id"): case for case in cases if isinstance(case, dict)}
    for phase in ("before", "after"):
        phase_data = part_c.get(phase, {})
        if not isinstance(phase_data, dict):
            errors.append(f"part_c.{phase} must be an object")
            continue
        mapped_case = by_case.get(phase_data.get("case_id"))
        if mapped_case is None:
            errors.append(f"part_c.{phase} references an unknown case")
        elif mapped_case.get("trace_id") != phase_data.get("trace_id"):
            errors.append(f"part_c.{phase} trace_id does not match its case")
    for relative_path in part_c.get("evidence") or []:
        if not isinstance(relative_path, str) or not (ROOT / relative_path).is_file():
            errors.append(f"part_c evidence is missing: {relative_path}")

    return errors


def main() -> int:
    try:
        errors = validate()
    except (OSError, ValueError) as exc:
        print(f"Homework 1 specification validation failed: {exc}", file=sys.stderr)
        return 1

    if errors:
        print("Homework 1 specification validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    mapping = load_json(MAPPING_PATH)
    print(
        "Homework 1 specification validation passed: "
        f"{mapping['source_log']['rows']} evidence rows, "
        f"{len(load_json(READINESS_PATH)['requirements'])} canonical requirements, "
        f"specification {mapping['specification']['version']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

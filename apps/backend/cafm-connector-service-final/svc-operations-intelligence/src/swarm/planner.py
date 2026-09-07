"""Planner role — decomposes Orchestrator instruction into sequenced / parallel tasks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

StreamName = Literal["building", "vendor", "contract", "energy"]


@dataclass
class TaskSpec:
    task_id: str
    worker: str
    stream: StreamName
    instruction: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class InstructionPacket:
    """Orchestrator → Planner handoff packet."""

    packet_id: str
    intent: str
    scope: str
    organization_id: str | None
    tasks: list[TaskSpec]
    parallel: bool = True


def plan_compliance_scan(
    *,
    scope: str = "all",
    organization_id: UUID | None = None,
    site_id: UUID | None = None,
    certificate_type_code: str | None = None,
) -> InstructionPacket:
    """
    Decompose compliance scan into two parallel streams (A2 building + A3 vendor).
    Each stream is assigned to a Worker with shared context.
    """
    ctx = {
        "scope": scope,
        "organization_id": str(organization_id) if organization_id else None,
        "site_id": str(site_id) if site_id else None,
        "certificate_type_code": certificate_type_code,
    }
    tasks: list[TaskSpec] = []
    if scope in {"all", "building"}:
        tasks.append(
            TaskSpec(
                task_id=str(uuid4()),
                worker="building_cert_worker",
                stream="building",
                instruction=(
                    "Fetch Building ComplianceCertificate records → compute status → "
                    "generate alert ladder → draft booking/email → Adversary on Lapsed"
                ),
                context={**ctx, "cert_scope": "Building"},
            )
        )
    if scope in {"all", "vendor"}:
        tasks.append(
            TaskSpec(
                task_id=str(uuid4()),
                worker="vendor_cert_worker",
                stream="vendor",
                instruction=(
                    "Fetch Vendor accreditation records → risk level → email drafts → "
                    "block_state for Lapsed → Adversary on email/block"
                ),
                context={**ctx, "cert_scope": "Vendor"},
            )
        )
    return InstructionPacket(
        packet_id=str(uuid4()),
        intent="compliance_scan",
        scope=scope,
        organization_id=str(organization_id) if organization_id else None,
        tasks=tasks,
        parallel=True,
    )


def packet_to_dict(packet: InstructionPacket) -> dict[str, Any]:
    return {
        "packet_id": packet.packet_id,
        "intent": packet.intent,
        "scope": packet.scope,
        "organization_id": packet.organization_id,
        "parallel": packet.parallel,
        "tasks": [
            {
                "task_id": t.task_id,
                "worker": t.worker,
                "stream": t.stream,
                "instruction": t.instruction,
                "context": t.context,
            }
            for t in packet.tasks
        ],
    }

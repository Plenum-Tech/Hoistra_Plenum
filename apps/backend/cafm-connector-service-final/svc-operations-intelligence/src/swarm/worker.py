"""Worker role — executes a single discrete swarm task with structured output."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

WorkerKind = Literal["building_cert_worker", "vendor_cert_worker"]


@dataclass
class WorkerResult:
    worker: str
    stream: str
    ok: bool
    scanned: int = 0
    alerts_created: int = 0
    blocks_set: int = 0
    adversary_passed: int = 0
    adversary_failed: int = 0
    adversary_flagged: int = 0
    adversary_escalated: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker": self.worker,
            "stream": self.stream,
            "ok": self.ok,
            "scanned": self.scanned,
            "alerts_created": self.alerts_created,
            "blocks_set": self.blocks_set,
            "adversary_passed": self.adversary_passed,
            "adversary_failed": self.adversary_failed,
            "adversary_flagged": self.adversary_flagged,
            "adversary_escalated": self.adversary_escalated,
            "details": self.details[:200],
            "error": self.error,
        }


def merge_worker_results(results: list[WorkerResult]) -> dict[str, Any]:
    merged = {
        "building_scanned": 0,
        "vendor_scanned": 0,
        "alerts_created": 0,
        "blocks_set": 0,
        "adversary_passed": 0,
        "adversary_failed": 0,
        "adversary_flagged": 0,
        "adversary_escalated": 0,
        "details": [],
        "streams": [],
    }
    for r in results:
        merged["streams"].append(r.stream)
        if r.stream == "building":
            merged["building_scanned"] += r.scanned
        elif r.stream == "vendor":
            merged["vendor_scanned"] += r.scanned
        merged["alerts_created"] += r.alerts_created
        merged["blocks_set"] += r.blocks_set
        merged["adversary_passed"] += r.adversary_passed
        merged["adversary_failed"] += r.adversary_failed
        merged["adversary_flagged"] += r.adversary_flagged
        merged["adversary_escalated"] += r.adversary_escalated
        merged["details"].extend(r.details[:250])
    return merged

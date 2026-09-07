from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, Column, String, DateTime, JSON, Integer, Numeric, Text, func
from .base import Base


class JourneyLog(Base):
    __tablename__ = "wo_journey_logs"
    __table_args__ = {"schema": "plenum_cafm"}

    jlog_id           = Column(String(50),  primary_key=True)
    work_order_id     = Column(String(50),  nullable=False)
    status            = Column(String(50),  default="active")       # active | completed | cancelled
    milestones        = Column(JSON,        default=list)           # [{name, status, timestamp, notes}]
    expected_timeline = Column(JSON,        default=dict)           # {start, expected_end, duration_hours}
    events            = Column(JSON,        default=list)
    current_step      = Column(String(100))
    deviations        = Column(JSON,        default=list)
    completed         = Column(Boolean,      default=False)

    # BE2-03: timeline tracking
    actual_start      = Column(DateTime(timezone=True))
    actual_end        = Column(DateTime(timezone=True))

    # BE2-03: extended tracking fields
    asset_id                    = Column(String(50))
    source_system               = Column(String(50))
    journey_status              = Column(String(50))                # in_progress | completed | failed
    assigned_technician_id      = Column(String(100))
    assigned_technician_name    = Column(String(255))
    team_members                = Column(JSON)
    estimated_cost              = Column(Numeric(15, 2))
    actual_cost                 = Column(Numeric(15, 2))
    estimated_duration_hours    = Column(Integer)
    actual_duration_hours       = Column(Integer)
    resources_used              = Column(JSON)
    completion_quality_score    = Column(Integer)
    customer_satisfaction_score = Column(Integer)
    notes                       = Column(Text)
    status_change_history       = Column(JSON)
    milestone_history           = Column(JSON)
    created_by                  = Column(String(100))
    updated_by                  = Column(String(100))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # ── Helper methods ────────────────────────────────────────────────────────

    def get_completion_percentage(self) -> float:
        milestones = self.milestones or []
        if not milestones:
            return 0.0
        done = sum(1 for m in milestones if m.get("status") == "completed")
        return round((done / len(milestones)) * 100, 1)

    def update_milestone(self, milestone_name: str, new_status: str) -> None:
        milestones = list(self.milestones or [])
        now_iso = datetime.now(timezone.utc).isoformat()
        for m in milestones:
            if m.get("name") == milestone_name:
                m["status"] = new_status
                if new_status in ("completed", "current"):
                    m["timestamp"] = now_iso
                break
        self.milestones = milestones
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "jlog_id":                      self.jlog_id,
            "work_order_id":                self.work_order_id,
            "status":                       self.status,
            "journey_status":               self.journey_status,
            "milestones":                   self.milestones,
            "expected_timeline":            self.expected_timeline,
            "current_step":                 self.current_step,
            "completed":                    self.completed,
            "completion_percentage":        self.get_completion_percentage(),
            "asset_id":                     self.asset_id,
            "source_system":                self.source_system,
            "assigned_technician_id":       self.assigned_technician_id,
            "assigned_technician_name":     self.assigned_technician_name,
            "team_members":                 self.team_members,
            "estimated_cost":               float(self.estimated_cost) if self.estimated_cost is not None else None,
            "actual_cost":                  float(self.actual_cost) if self.actual_cost is not None else None,
            "estimated_duration_hours":     self.estimated_duration_hours,
            "actual_duration_hours":        self.actual_duration_hours,
            "actual_start":                 self.actual_start.isoformat() if self.actual_start else None,
            "actual_end":                   self.actual_end.isoformat() if self.actual_end else None,
            "resources_used":               self.resources_used,
            "completion_quality_score":     self.completion_quality_score,
            "customer_satisfaction_score":  self.customer_satisfaction_score,
            "notes":                        self.notes,
            "deviations":                   self.deviations,
            "status_change_history":        self.status_change_history,
            "milestone_history":            self.milestone_history,
            "created_by":                   self.created_by,
            "updated_by":                   self.updated_by,
            "created_at":                   self.created_at.isoformat() if self.created_at else None,
            "updated_at":                   self.updated_at.isoformat() if self.updated_at else None,
        }

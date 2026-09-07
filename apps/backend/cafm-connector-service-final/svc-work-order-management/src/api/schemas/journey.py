from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class MilestoneUpdate(BaseModel):
    milestone_name: str
    status:         str        # pending | current | completed | skipped
    notes:          Optional[str] = None


class JourneyResponse(BaseModel):
    jlog_id:           str
    work_order_id:     str
    status:            Optional[str]
    journey_status:    Optional[str]
    milestones:        Optional[list]
    expected_timeline: Optional[dict]
    current_step:      Optional[str]
    completed:         Optional[str]

    # BE2-03: extended fields
    asset_id:                    Optional[str]     = None
    source_system:               Optional[str]     = None
    assigned_technician_id:      Optional[str]     = None
    assigned_technician_name:    Optional[str]     = None
    team_members:                Optional[list]    = None
    estimated_cost:              Optional[float]   = None
    actual_cost:                 Optional[float]   = None
    estimated_duration_hours:    Optional[int]     = None
    actual_duration_hours:       Optional[int]     = None
    actual_start:                Optional[datetime] = None
    actual_end:                  Optional[datetime] = None
    resources_used:              Optional[list]    = None
    completion_quality_score:    Optional[int]     = None
    customer_satisfaction_score: Optional[int]     = None
    notes:                       Optional[str]     = None
    status_change_history:       Optional[dict]    = None
    milestone_history:           Optional[dict]    = None
    created_by:                  Optional[str]     = None
    updated_by:                  Optional[str]     = None

    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    model_config = {"from_attributes": True}


class StatusHistoryEntry(BaseModel):
    history_id:    str
    work_order_id: str
    from_status:   Optional[str]
    to_status:     str
    changed_by:    Optional[str]
    notes:         Optional[str]
    changed_at:    Optional[datetime]

    model_config = {"from_attributes": True}


class DashboardStats(BaseModel):
    total:              int
    by_status:          dict[str, int]
    by_priority:        dict[str, int]
    by_source:          dict[str, int]
    created_today:      int
    assets_by_category: dict[str, int] = {}


class JourneyAnalytics(BaseModel):
    total_journeys:             int
    completed:                  int
    active:                     int
    in_progress_journeys:       int = 0
    failed_journeys:            int = 0
    completion_rate:            float
    avg_completion_hours:       Optional[float]
    milestone_completion_rates: dict[str, float]


class BulkStatusUpdate(BaseModel):
    work_order_ids: list[str]
    new_status:     str
    notes:          Optional[str] = None


class JourneyHealth(BaseModel):
    health_status:        str
    completion_percentage: float
    time_overrun_hours:   int
    cost_overrun:         float
    on_track:             bool
    requires_attention:   bool

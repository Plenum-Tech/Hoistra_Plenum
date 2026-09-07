from pydantic import BaseModel
from typing import Optional, List


class PPMSchedule(BaseModel):
    schedule_id: str
    asset_id: str
    asset_name: str
    location: str
    task_description: str
    task_type: str
    frequency: str  # daily | weekly | monthly | quarterly | annually
    priority: str = "medium"
    estimated_duration_minutes: int = 60
    required_skills: List[str] = []
    required_tools: List[str] = []
    required_parts: List[str] = []
    safety_requirements: List[str] = []
    last_executed: Optional[str] = None

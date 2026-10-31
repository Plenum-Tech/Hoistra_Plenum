"""Typed manual reports and immutable snapshot reads; no telemetry injection tools."""
from datetime import date
from pydantic import BaseModel,ConfigDict,Field
class Report(BaseModel):
    model_config=ConfigDict(extra='forbid')
    period_start:date
    building_ids:list[str]|None=Field(default=None,min_length=1,max_length=100)
class Read(BaseModel):
    model_config=ConfigDict(extra='forbid')
    report_id:str=Field(min_length=1,max_length=160)
MODELS={'usage_weekly_report':Report,'usage_monthly_report':Report,'usage_get_report':Read}
ROLE_TOOLS={r:sorted(MODELS) for r in ['general_user','property_manager','portfolio_manager','compliance_officer']}

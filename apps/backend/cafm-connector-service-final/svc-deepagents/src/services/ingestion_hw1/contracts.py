"""Declared intake adapters; no arbitrary table, field or source mutation inputs."""
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid")


class Asset(Strict):
    asset_id:str=Field(min_length=1,max_length=160)
    condition_score:int=Field(ge=1,le=5,strict=True)
    condition_observation:str=Field(min_length=1,max_length=4000)


class Inspection(Strict):
    inspection_id:str=Field(min_length=1,max_length=160)
    inspection_date:date
    result:str=Field(min_length=1,max_length=160)
    summary:str=Field(min_length=1,max_length=4000)


class WorkReport(Strict):
    external_reference:str=Field(min_length=1,max_length=160)
    raised_at:datetime
    responded_at:datetime | None=None
    completed_at:datetime | None=None
    status_summary:str=Field(min_length=1,max_length=1000)
    vendor_id:str=Field(min_length=1,max_length=160)
    asset_id:str=Field(min_length=1,max_length=160)

    @model_validator(mode="after")
    def ordered(self):
        for value in (self.raised_at,self.responded_at,self.completed_at):
            if value and value.tzinfo is None: raise ValueError("Source timestamps require timezones")
        if any(value and value<self.raised_at for value in (self.responded_at,self.completed_at)):
            raise ValueError("Report dates are contradictory")
        return self


class Reading(Strict):
    meter_id:str=Field(min_length=1,max_length=160)
    timestamp:datetime
    consumption:Decimal=Field(ge=0,allow_inf_nan=False)
    unit:str=Field(pattern=r"^(kWh|m3|Wh)$")
    interval:str=Field(pattern=r"^(hour|half_hour|day|period)$")

    @model_validator(mode="after")
    def aware(self):
        if self.timestamp.tzinfo is None: raise ValueError("Reading timezone is required")
        return self


ADAPTERS={"asset_summary":Asset,"inspection_report":Inspection,"work_order_report":WorkReport,"meter_reading":Reading}
KEYS={"asset_summary":("asset_id",),"inspection_report":("inspection_id",),
      "work_order_report":("external_reference",),"meter_reading":("meter_id","timestamp","interval")}


def validated(kind,fields):
    if kind not in ADAPTERS: raise ValueError("Unsupported typed adapter")
    return ADAPTERS[kind].model_validate(fields).model_dump(mode="json")


def identity(kind,fields):
    return "|".join(str(fields[k]) for k in KEYS[kind])


class Search(Strict):
    building_id:str=Field(min_length=1,max_length=160)
    query:str=Field(default="",max_length=250)
    include_history:bool=False
    limit:int=Field(default=50,ge=1,le=100)


class RecordQuery(Strict):
    record_id:str=Field(min_length=1,max_length=160)

class SourceQuery(Strict):
    source_id:str=Field(min_length=1,max_length=160)


class Intake(Strict):
    building_id:str=Field(min_length=1,max_length=160)
    kind:str
    original:str=Field(min_length=1,max_length=1000000)
    fields:dict


class Correction(Strict):
    building_id:str=Field(min_length=1,max_length=160)
    record_id:str=Field(min_length=1,max_length=160)
    original:str=Field(min_length=1,max_length=1000000)
    fields:dict
    reason:str=Field(default="Evidence-backed derived correction proposed for review",min_length=8,max_length=1000)


class Building(Strict):
    original:str=Field(min_length=1,max_length=1000000)
    fields:dict

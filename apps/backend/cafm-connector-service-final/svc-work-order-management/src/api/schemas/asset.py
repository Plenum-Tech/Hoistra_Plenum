from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime


class AssetCreate(BaseModel):
    asset_id:      str
    asset_name:    str
    asset_type:    Optional[str] = None
    location:      Optional[str] = None
    manufacturer:  Optional[str] = None
    model:         Optional[str] = None
    serial_number: Optional[str] = None

    @field_validator("asset_name", mode="before")
    @classmethod
    def no_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("asset_name must not be blank")
        return v.strip()


class AssetResponse(BaseModel):
    asset_id:      str
    asset_name:    str
    asset_type:    Optional[str] = None
    location:      Optional[str] = None
    manufacturer:  Optional[str] = None
    model:         Optional[str] = None
    serial_number: Optional[str] = None
    active:        bool = True
    created_at:    Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("asset_id", mode="before")
    @classmethod
    def coerce_to_str(cls, v) -> str:
        return str(v) if v is not None else v


class LocationResponse(BaseModel):
    location_id: str
    name:        str
    building:    Optional[str] = None
    floor:       Optional[str] = None
    zone:        Optional[str] = None
    active:      bool = True

    model_config = {"from_attributes": True}

    @field_validator("location_id", mode="before")
    @classmethod
    def coerce_to_str(cls, v) -> str:
        return str(v) if v is not None else v

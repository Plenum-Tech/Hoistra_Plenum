from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import date, datetime


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
    building_id:   Optional[str] = None
    asset_name:    str
    asset_code:    Optional[str] = None
    asset_type:    Optional[str] = None
    location:      Optional[str] = None
    manufacturer:  Optional[str] = None
    model:         Optional[str] = None
    serial_number: Optional[str] = None
    active:        bool = True
    status:        Optional[str] = None
    created_at:    Optional[datetime] = None

    # category_id is a real foreign key; category_name is resolved alongside it so a page
    # showing assets does not have to call a second service to turn a UUID into a word.
    category_id:       Optional[str] = None
    category_name:     Optional[str] = None
    location_id:       Optional[str] = None
    criticality:       Optional[str] = None
    #: integer 0-100 on the table, with a check constraint. Not a float.
    health_score:      Optional[int] = None
    installation_date: Optional[date] = None
    warranty_expiry:   Optional[date] = None
    #: The condition trio travels together. An undated score reads as current when it may
    #: be years old, so the date is part of the answer rather than an extra.
    condition_score:      Optional[int] = None
    condition_provenance: Optional[dict] = None
    condition_updated_at: Optional[datetime] = None
    #: What the failure model and the value-at-risk figure are computed from. Returned in the
    #: LIST response on purpose: the page bands every asset at once, and a field it has to open
    #: each asset to read cannot be used for that. Without these the register showed "Est. asset
    #: value at risk GBP0 — 0 of 12 assets contributing" over assets that carried all of it.
    replacement_value: Optional[float] = None
    design_life_years: Optional[float] = None
    wear_coefficient:  Optional[float] = None
    #: The section of the building the asset sits in, so the page can group without a second call.
    section_id:        Optional[str] = None
    #: The vendor who holds the asset, for the same reason.
    vendor_id:         Optional[str] = None

    model_config = {"from_attributes": True}

    @field_validator("replacement_value", "design_life_years", "wear_coefficient",
                     mode="before")
    @classmethod
    def coerce_decimal(cls, v):
        """Numeric comes back as Decimal, which is not JSON."""
        return float(v) if v is not None else v

    @field_validator("asset_id", "building_id", "category_id", "location_id", "section_id",
                     "vendor_id", mode="before")
    @classmethod
    def coerce_to_str(cls, v) -> str:
        return str(v) if v is not None else v

    @field_validator("health_score", "condition_score", mode="before")
    @classmethod
    def coerce_number(cls, v):
        return int(v) if v is not None else v


class AssetCategoryResponse(BaseModel):
    """An asset category, resolved by a service that knows who is asking."""

    category_id:        str
    name:               Optional[str] = None
    description:        Optional[str] = None
    parent_category_id: Optional[str] = None
    asset_count:        int = 0


class LocationResponse(BaseModel):
    location_id: str
    building_id: Optional[str] = None
    name:        str
    building:    Optional[str] = None
    floor:       Optional[str] = None
    zone:        Optional[str] = None
    active:      bool = True

    model_config = {"from_attributes": True}

    @field_validator("location_id", "building_id", mode="before")
    @classmethod
    def coerce_to_str(cls, v) -> str:
        return str(v) if v is not None else v

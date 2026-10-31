"""Exact preview and explicit confirmation for scoped source ingestion."""
from fastapi import APIRouter, HTTPException, Request
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from ...services.ingestion_hw1.service import IngestionService,ROLE_TOOLS

router=APIRouter(prefix="/api/homework/ingestion",tags=["Isolated ingestion"])

class ToolRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    tool:str=Field(min_length=1,max_length=100)
    arguments:dict=Field(default_factory=dict)

@router.get("/tools")
async def tools(request:Request):
    if not getattr(request.app.state,"hw1_ingestion_enabled",False): raise HTTPException(503,"Ingestion profile not enabled")
    return {"tools":sorted(ROLE_TOOLS.get(request.state.test_principal.role,[]))}

@router.post("/tools/execute")
async def execute(body:ToolRequest,request:Request):
    if not getattr(request.app.state,"hw1_ingestion_enabled",False): raise HTTPException(503,"Ingestion profile not enabled")
    try: return await IngestionService().execute(request.state.test_principal,body.tool,body.arguments,request.state.request_id)
    except SQLAlchemyError: raise HTTPException(503,"Ingestion store unavailable; no result claimed") from None

class PreviewRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    building_id:str=Field(min_length=1,max_length=100)
    kind:str=Field(min_length=1,max_length=50)
    original:str=Field(min_length=1,max_length=1_000_000)
    fields:dict

class ConfirmRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    preview_hash:str=Field(pattern=r"^[0-9a-f]{64}$")
    reason:str=Field(min_length=8,max_length=1000)

class ConflictResolutionRequest(ConfirmRequest):
    decision:Literal["keep_current","accept_incoming"]

class CorrectionPreviewRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    building_id:str=Field(min_length=1,max_length=100)
    record_id:str=Field(min_length=1,max_length=200)
    original:str=Field(min_length=1,max_length=1_000_000)
    fields:dict
    reason:str=Field(default="Evidence-backed derived correction proposed for review",min_length=8,max_length=1000)

class BuildingOnboardingRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    original:str=Field(min_length=1,max_length=1_000_000)
    fields:dict

@router.post("/previews")
async def preview(body:PreviewRequest,request:Request):
    if not getattr(request.app.state,"hw1_ingestion_enabled",False): raise HTTPException(503,"Ingestion profile not enabled")
    try: return await IngestionService().preview(request.state.test_principal,body.building_id,body.kind,body.original,body.fields)
    except SQLAlchemyError: raise HTTPException(503,"Ingestion store unavailable; no source accepted") from None

@router.post("/buildings/previews")
async def building_preview(body:BuildingOnboardingRequest,request:Request):
    if not getattr(request.app.state,"hw1_ingestion_enabled",False): raise HTTPException(503,"Ingestion profile not enabled")
    try:
        return await IngestionService().preview_building(request.state.test_principal,
            body.original,body.fields)
    except SQLAlchemyError: raise HTTPException(503,"Building onboarding store unavailable; no building created") from None

@router.post("/buildings/previews/{identifier}/confirm")
async def building_confirm(identifier:str,body:ConfirmRequest,request:Request):
    if not getattr(request.app.state,"hw1_ingestion_enabled",False): raise HTTPException(503,"Ingestion profile not enabled")
    try:
        return await IngestionService().confirm_building(request.state.test_principal,identifier,
            body.preview_hash,body.reason,request.state.request_id)
    except SQLAlchemyError: raise HTTPException(503,"Building onboarding store unavailable; no building created") from None

@router.post("/previews/{identifier}/confirm")
async def confirm(identifier:str,body:ConfirmRequest,request:Request):
    if not getattr(request.app.state,"hw1_ingestion_enabled",False): raise HTTPException(503,"Ingestion profile not enabled")
    try: return await IngestionService().confirm(request.state.test_principal,identifier,body.preview_hash,body.reason,request.state.request_id)
    except SQLAlchemyError: raise HTTPException(503,"Ingestion store unavailable; no source accepted") from None

@router.post("/conflicts/{identifier}/resolve")
async def resolve_conflict(identifier:str,body:ConflictResolutionRequest,request:Request):
    if not getattr(request.app.state,"hw1_ingestion_enabled",False): raise HTTPException(503,"Ingestion profile not enabled")
    try:
        return await IngestionService().resolve_conflict(request.state.test_principal,identifier,
            body.preview_hash,body.decision,body.reason,request.state.request_id)
    except SQLAlchemyError: raise HTTPException(503,"Conflict review store unavailable; no version selected") from None

@router.post("/corrections/previews")
async def correction_preview(body:CorrectionPreviewRequest,request:Request):
    if not getattr(request.app.state,"hw1_ingestion_enabled",False): raise HTTPException(503,"Ingestion profile not enabled")
    try: return await IngestionService().preview_correction(request.state.test_principal,body.building_id,body.record_id,body.original,body.fields,body.reason)
    except SQLAlchemyError: raise HTTPException(503,"Correction store unavailable; no change proposed") from None

@router.post("/corrections/previews/{identifier}/confirm")
async def correction_confirm(identifier:str,body:ConfirmRequest,request:Request):
    if not getattr(request.app.state,"hw1_ingestion_enabled",False): raise HTTPException(503,"Ingestion profile not enabled")
    try: return await IngestionService().confirm_correction(request.state.test_principal,identifier,body.preview_hash,body.reason,request.state.request_id)
    except SQLAlchemyError: raise HTTPException(503,"Correction store unavailable; no change claimed") from None

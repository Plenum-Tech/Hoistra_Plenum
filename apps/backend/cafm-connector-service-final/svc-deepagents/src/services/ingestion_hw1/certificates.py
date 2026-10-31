"""Certificate-only bridge reuses the existing guarded Compliance confirmation."""
import json
from uuid import uuid4
from fastapi import HTTPException
from .index import project


async def prepare_certificate(service,principal,building,record_id,original,changes,reason):
    from ..compliance_hw1.contracts import CertificateValues,ProposalInput
    from ..compliance_hw1.actions import prepare,evidence_fields
    async with service.store.transaction(principal) as repo:
        try:
            rows=await repo.scoped("records",[building] if building in principal.building_ids else [],["certificate"],record_id)
        except HTTPException as error:
            if error.status_code!=404: raise
            rows=[]
        if not rows:
            if principal.role=="compliance_officer": raise HTTPException(403,"Officer corrections are certificate-only")
            return None
        row=rows[0]
        if not changes or set(changes)-set(CertificateValues.model_fields):
            raise HTTPException(422,"Only declared certificate fields may be corrected")
        values={k:row["data"].get(k) for k in CertificateValues.model_fields}
        values.update(changes)
        try:
            values=CertificateValues.model_validate(values).model_dump(mode="json")
            if set(json.loads(original))!={"fields"} or evidence_fields(original)!=values:
                raise ValueError
        except (ValueError,TypeError):
            raise HTTPException(422,"Certificate correction needs complete matching structured evidence") from None
        source_id="certificate-correction-draft-"+uuid4().hex
        await repo.insert("sources","certificate",[building],{"original":original,"text":original,
            "fields":values,"version":1,"information_type":"certificate","scope_verified":True},source_id)
        body=ProposalInput(action="correct_certificate",entity_id=record_id,values=values,source_id=source_id,
            reason=reason)
        result=await prepare(repo,principal,body)
        await project(repo,row)
        return result


async def confirm_certificate(service,principal,proposal_id,expected_hash,reason):
    from ..compliance_hw1.service import ComplianceService
    async with service.store.transaction(principal) as repo:
        rows=[r for r in await repo.list("proposals","compliance") if r["id"]==proposal_id]
        if not rows:
            if principal.role=="compliance_officer": raise HTTPException(403,"Officer corrections are certificate-only")
            return None
        proposal=rows[0]
        if proposal["data"]["request"]["action"]!="correct_certificate":
            raise HTTPException(409,"Wrong certificate confirmation workflow")
        if proposal["data"]["request"]["reason"]!=reason:
            raise HTTPException(409,"Certificate reason changed; prepare and confirm its exact preview")
        version=proposal["data"]["version"]
    return await ComplianceService(service.store).confirm(principal,proposal_id,{
        "version":version,"preview_hash":expected_hash,"idempotency_key":"ingestion-cert-"+proposal_id,
        "decision":"approve"})

"""Authorized accepted inputs trigger an explainable fixed-HOI evaluation, never guessed slots."""
from datetime import datetime,timezone
from ..compliance_hw1.hoi import building_score,FORMULA
from ..compliance_hw1.outbox import notify_managers


async def review_notice(repo,building,event,proposal_id):
    return await notify_managers(repo,building,event,{"proposal_id":proposal_id,
        "review":"An eligible assigned manager may review retained evidence and explicitly decide."},"ingestion_review")


async def changed(repo,principal,building,event,source_id,record_id,reason):
    manifests=[r for r in await repo.list("records","hoi_manifest") if r["id"]==building+"-hoi-manifest"]
    if not manifests:
        result={"status":"unavailable","reason":"fixed HOI manifest is unavailable; no score claimed"}
        await repo.insert("history","hoi_recalculation_failed",[building],{
            **result,"event_id":event,"actor":principal.user_id,"source_id":source_id},"hoi-failed-"+event)
        return result
    score=await building_score(repo,building)
    prior=sorted([r["data"] for r in await repo.list("history","hoi") if r["buildings"]==[building]],key=lambda x:x.get("time",""))
    before=prior[-1].get("score",score["score"]) if prior else score["score"]
    data={"event_id":event,"building_id":building,"before":before,"after":score["score"],"score":score["score"],
        "formula_version":FORMULA,"actor":principal.user_id,"reason":reason,"source_id":source_id,
        "record_id":record_id,"time":datetime.now(timezone.utc).isoformat(),
        "checklist_version":score["checklist_version"],"source_versions":score["source_versions"],
        "mapping":"Unlinked evidence never invents a fixed HOI slot; an unchanged recalculation is retained."}
    await repo.insert("history","hoi",[building],data,"hoi-"+event)
    notices=await notify_managers(repo,building,event,data,"hoi")
    return {"status":"recalculated","before":before,"after":score["score"],"notifications":notices}

#!/usr/bin/env python3
"""Live isolated Contract milestone gate. No mocks, credentials or source bodies in receipts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

MODULE = Path(__file__).resolve().parents[2]
ROOT = MODULE.parents[1]
BASE = "http://127.0.0.1:18009"
DESTINATION = MODULE / "milestones/evidence/01-contract.json"
REQUIRED = ["source_period_scope_compatibility", "independent_scoring_and_date_cap",
    "portfolio_configuration_confirmation_and_snapshot", "invoice_diagnostic_separate_from_vendor_score",
    "role_and_building_denials", "skill_tool_agent_and_live_provider_integration"]
BUILDING = "hw1-building-a1"
VENDOR = BUILDING + "-vendor-0"


class GateFailure(Exception):
    """A safe public failure description with no response/source body."""


def require(value, message):
    if not value:
        raise GateFailure(message)


def key():
    return "milestone-contract-" + uuid4().hex


def read_sessions():
    path = MODULE / "runtime/test-session-tokens.json"
    require(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, "Private session file permissions are too broad")
    return json.loads(path.read_text())


class Gate:
    def __init__(self):
        self.tokens = read_sessions()
        self.checks = []
        self.baseline = None

    def call(self, user, path, body=None, expected=200):
        headers = {"Content-Type": "application/json"}
        if user:
            headers["Authorization"] = "Bearer " + self.tokens[user]
        request = urllib.request.Request(BASE + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers,
            method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                status, payload = response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            status, payload = error.code, json.loads(error.read())
        except (OSError, ValueError):
            raise GateFailure("Candidate API request failed: " + path.split("?")[0]) from None
        require(status == expected, f"{path.split('?')[0]} expected HTTP {expected}, observed {status}")
        return payload

    def execute(self, tool, arguments, user="hw1-portfolio", expected=200):
        return self.call(user, "/api/homework/contract/tools/execute",
                         {"tool": tool, "arguments": arguments}, expected)

    def score(self, vendor=VENDOR, **changes):
        return {"vendor_id": vendor, "period_start": "2026-10-01", "period_end": "2026-10-31",
                "building_ids": [BUILDING], "idempotency_key": key(), **changes}

    def config(self):
        return self.execute("contract_scoring_configuration", {})["result"]

    def preview(self, parameters):
        return self.execute("contract_prepare_scoring_configuration",
            {**parameters, "reason": "Fictional milestone verification; preserve prior report snapshots"})["result"]

    def confirm(self, preview, confirmation=None, user="hw1-portfolio", expected=200):
        body = confirmation or {"preview_hash": preview["preview_hash"],
            "expected_version": preview["current_version"], "idempotency_key": key()}
        return self.call(user, "/api/homework/contract/configuration/proposals/" + preview["proposal_id"] + "/confirm", body, expected)

    def record(self, identifier, evidence):
        self.checks.append({"id": identifier, "passed": True, "evidence": evidence})

    def source_and_scope(self):
        status = self.call("hw1-portfolio", "/api/homework/contract/status")
        require(status["enabled"] and status["specification_version"] == "0.37.0", "Wrong Contract profile")
        q = self.score()
        self.baseline = self.execute("contract_vendor_performance", q)["result"]
        result = self.baseline
        require(result["building_scope"] == [BUILDING] and result["sla_time_basis"] == "elapsed_hours", "Wrong scope/time basis")
        require(result["source_ids"] and set(result["source_ids"]) == set(result["source_versions"]), "Missing source versions")
        require(all(version == 1 for version in result["source_versions"].values()), "Unexpected source version")
        self.execute("contract_vendor_performance", self.score(period_start="2026-10-02"), expected=422)
        self.execute("contract_vendor_performance", self.score("unknown-vendor"), expected=404)
        self.execute("contract_vendor_performance", self.score(BUILDING + "-vendor-902"), expected=409)
        for number, reason in [(903, "contract_target_missing"), (904, "accreditation_expiry_unknown"),
                               (906, "same_signing_date_contract_tie_requires_review")]:
            edge = self.execute("contract_vendor_performance", self.score(BUILDING + "-vendor-" + str(number)))["result"]
            require(edge["overall_score"] is None and edge["eligible_work_orders"] == 0
                and any(row["reason"] == reason for row in edge["excluded_work_orders"]), "Missing explicit unassessable exclusion")
        empty = self.execute("contract_vendor_performance", self.score(period_start="2026-09-01", period_end="2026-09-30"))["result"]
        require(empty["overall_score"] is None and empty["eligible_work_orders"] == 0, "Empty month fabricated a score")
        self.record(REQUIRED[0], "Candidate source-backed October scorecard has exact A1 scope, elapsed-hour SLA basis and source/version lineage. Partial-month input=422; unknown vendor=404; deliberately mismatched derived mirror=409. Working-day-only target, unknown accreditation expiry and same-signing-date contract tie each produce a visible exclusion and null score. Empty September score is null. Fixture refs: contract-fixtures-v3 and hw1-contract-edge-*.")

    def arithmetic(self):
        result = self.baseline
        require(result["overall_score"] == 53 and result["eligible_work_orders"] == 3, "Expected (65+35+60)/3 half-up = 53")
        detail = result["work_order_details"]
        require(sorted(row["score"] for row in detail.values()) == [35, 60, 65], "Wrong independently expected per-order contributions")
        require(sum(row["accreditation_cap_applied"] for row in detail.values()) == 2, "Wrong date-specific cap count")
        require(result["ppm"]["compliance_pct"] == "100" and result["ppm"]["visit_count"] == 1, "PPM tolerance result incorrect")
        boundary = self.execute("contract_vendor_performance", self.score(BUILDING + "-vendor-901"))["result"]
        require(boundary["overall_score"] == 80, "Expiry-day100 and lapse-day60 should average80")
        require(not boundary["work_order_details"]["hw1-contract-edge-expiry-day-901"]["accreditation_cap_applied"]
            and boundary["work_order_details"]["hw1-contract-edge-lapse-day-901"]["accreditation_cap_applied"], "Expiry-day incorrectly capped")
        recall = self.execute("contract_vendor_performance", self.score(BUILDING + "-vendor-905"))["result"]
        require(recall["overall_score"] == 65 and recall["eligible_work_orders"] == 1, "Cross-month recall expected65")
        concurrent = self.score()
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda _: self.execute("contract_vendor_performance", concurrent)["result"], range(2)))
        require(rows[0]["scorecard_id"] == rows[1]["scorecard_id"], "Concurrent identical scorecards not idempotent")
        self.execute("contract_vendor_performance", {**concurrent, "vendor_id": BUILDING + "-vendor-901"}, expected=409)
        self.record(REQUIRED[1], "Independent five-component contributions 65,35,60 average to53 (half-up); exactly two contributions have evidenced date caps. Separate boundary fixture returns expiry day100 and following lapse day60 =>80. Cross-month same-asset recall returns65. PPM visit +6 days passes inclusive +/-7 tolerance (1/1=100%). Two concurrent same-key requests return one scorecard ID; changed-key payload=409.")

    def configuration(self):
        current = self.config()
        snapshot = json.loads(json.dumps(self.baseline["parameter_snapshot"]))
        parameters = current["parameters"]
        proposed = {**parameters, "score_cap": 50}
        preview = self.preview(proposed)
        stale = self.preview({**parameters, "ppm_tolerance_days": 6})
        require(self.config()["version"] == current["version"] and self.config()["parameters"] == parameters, "Preview changed settings")
        require(preview["current_parameters"] == parameters and preview["proposed_parameters"]["score_cap"] == 50, "Incomplete exact preview")
        body = {"preview_hash": preview["preview_hash"], "expected_version": preview["current_version"], "idempotency_key": key()}
        self.confirm(preview, {**body, "preview_hash": "0"*64}, expected=409)
        self.confirm(preview, body, user="hw1-property", expected=403)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                confirms = list(pool.map(lambda _: self.confirm(preview, body), range(2)))
            require({x["outcome"] for x in confirms} == {"confirmed", "already_confirmed"}, "Config concurrent confirmation not idempotent")
            self.confirm(preview, {**body, "preview_hash": "0"*64}, expected=409)
            self.confirm(stale, expected=409)
            changed = self.execute("contract_vendor_performance", self.score())["result"]
            require(changed["overall_score"] == 50 and changed["parameter_snapshot"]["parameters"]["score_cap"] == 50, "New settings not applied with exact snapshot")
            old = self.call("hw1-portfolio", "/api/homework/contract/scorecards/" + self.baseline["scorecard_id"])["result"]
            require(old["overall_score"] == 53 and old["parameter_snapshot"] == snapshot, "Historical scorecard changed")
            invalid = {**parameters, "weights": {**parameters["weights"], "invoice_pct": 0}, "reason": "invalid component test"}
            self.execute("contract_prepare_scoring_configuration", invalid, expected=422)
            invalid = {**parameters, "weights": {**parameters["weights"], "first_fix_pct": 0}, "reason": "invalid total test"}
            self.execute("contract_prepare_scoring_configuration", invalid, expected=422)
        finally:
            if self.config()["parameters"] != parameters:
                self.confirm(self.preview(parameters))
        require(self.config()["parameters"] == parameters, "Original fictional configuration not restored")
        self.record(REQUIRED[2], "PM organization-wide preview shows before/after and changes nothing. Explicit Portfolio confirmation changes cap60->50; new score53->50 with exact new parameter version; prior53 score and snapshot remain byte-equivalent. Bad hash, stale version and changed confirmed retry=409; Property confirmation=403; sixth component and weights not totaling100=422. Concurrent confirm yields confirmed+already_confirmed. Original parameters restored through a new confirmed version; no source or prior scorecard rewritten.")

    def invoices(self):
        args = {k: v for k, v in self.score().items() if k != "idempotency_key"}
        report = self.execute("contract_invoice_diagnostic", args)["result"]
        invoice = report["invoices"][0]
        require((invoice["matched_count"], invoice["cleared_count"], invoice["flagged_count"], invoice["unmatched_count"]) == (2,1,2,1), "Wrong diagnostic populations")
        require(invoice["counts_overlap"] and report["score_component"] is False, "Invoice improperly treated as score component")
        require(invoice["line_diagnostics"][1]["delta_gbp"] in ("20", "20.00"), "Expected (90-80)*2 = GBP20")
        require(report.get("diagnostic_id") and report["source_versions"], "Diagnostic snapshot/provenance missing")
        high = self.execute("contract_invoice_diagnostic", {**args, "vendor_id": BUILDING + "-vendor-901"})["result"]["invoices"][0]["line_diagnostics"][0]
        require(high["delta_gbp"] in ("600", "600.00") and high["status"] == "flagged"
            and high["adversary_gate"]["required"] and high["adversary_gate"]["independent_arithmetic_agrees"], "Day-rate fallback/independent large delta check failed")
        mismatch = self.execute("contract_invoice_diagnostic", {**args, "vendor_id": BUILDING + "-vendor-907", "building_ids": [BUILDING,"hw1-building-a2"]})["result"]["invoices"][0]
        require(mismatch["matched_count"] == 0 and mismatch["flagged_count"] == 1, "Invoice matched another building")
        score = self.execute("contract_vendor_performance", self.score())["result"]
        require(score["overall_score"] == 53 and len(score["component_percentages"]) == 5, "Invoice altered vendor performance")
        self.record(REQUIRED[3], "Separate persisted invoice diagnostic returns matched2/cleared1/flagged2/unmatched1 with overlap definitions and source versions. Independent mismatch=(90-80)*2=GBP20. Day-rate fixture uses640/8=80; (380-80)*2=GBP600 remains flagged and independent arithmetic gate agrees. Same-portfolio cross-building invoice reference is unmatched. New vendor score remains53 with exactly five components after diagnostics; no payment action offered.")

    def denials(self):
        for user in ("hw1-general", "hw1-compliance"):
            require(self.call(user, "/api/homework/contract/tools")["tools"] == [], "Prohibited role sees tools")
            for tool in ("contract_vendor_performance", "contract_invoice_diagnostic", "contract_scoring_configuration"):
                self.execute(tool, self.score() if tool == "contract_vendor_performance" else {}, user, 403)
            self.call(user, "/api/homework/contract/scorecards/" + self.baseline["scorecard_id"], expected=403)
        self.execute("contract_vendor_performance", self.score(building_ids=["hw1-building-a2"]), "hw1-property", 403)
        self.execute("contract_vendor_performance", self.score(building_ids=[BUILDING,"unknown-building"]), expected=403)
        self.execute("contract_vendor_performance", self.score("hw1-building-b1-vendor-0"), expected=404)
        self.execute("contract_scoring_configuration", {}, "hw1-property", 403)
        self.execute("get_schema", {}, expected=403)
        self.call(None, "/api/homework/contract/tools", expected=401)
        allowed = self.execute("contract_vendor_performance", self.score(), "hw1-property")["result"]
        require(allowed["overall_score"] == 53, "Assigned Property Manager read denied")
        self.record(REQUIRED[4], "Fresh API checks: assigned Property and Portfolio Managers score53; General User/Compliance Officer tool projections empty, direct tools and scorecard route=403; Property org-config=403. A2/unknown mixed-building requests denied403; other-organization vendor unavailable404; raw schema tool403; unauthenticated tools401. No role or assignment changed.")

    def agent(self):
        result = self.call("hw1-portfolio", "/api/homework/agent/ask", {"message":
            "Generate the vendor performance scorecard for " + VENDOR + " in hw1-building-a1 for 2026-10-01 through 2026-10-31. Use contract_vendor_performance and report its verified result."})
        require(result.get("selected_route") == "contract_performance" and result.get("outcome") == "answered", "Contract agent did not route/answer")
        provider = result.get("provider_execution", {})
        require(provider.get("successful_responses", 0) > 0 and "contract_vendor_performance" in provider.get("selected_tools", []), "No genuine provider-selected scorecard tool call")
        scores = [x for x in result.get("tool_calls", []) if x.get("tool") == "contract_vendor_performance" and "output" in x]
        require(scores and scores[0]["output"]["result"]["overall_score"] == 53, "Provider call not grounded in expected53")
        metadata = result["prompt_metadata"]
        manifest_path = ROOT / "apps/backend/cafm-connector-service-final/svc-deepagents/skills/contract-manifest.json"
        require(metadata["manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest(), "Live manifest differs from verified local source")
        require(len(metadata["loaded_instructions"]) == 6 and metadata["specification_version"] == "0.37.0", "Domain/shared instructions missing")
        require("contract_prepare_scoring_configuration" in metadata["bound_tools"] and not any("confirm" in t for t in metadata["bound_tools"]), "Model can confirm a business action")
        invoice = self.call("hw1-portfolio", "/api/homework/agent/ask", {
            "message": "Run only the separate invoice diagnostic using contract_invoice_diagnostic for "
                + VENDOR + " in hw1-building-a1 from 2026-10-01 through 2026-10-31. Show matched, cleared, flagged and unmatched counts.",
            "conversation_id": result["conversation_id"]})
        require(invoice.get("outcome") == "answered" and "contract_invoice_diagnostic" in
            invoice.get("provider_execution", {}).get("selected_tools", []), "No genuine provider-selected invoice diagnostic")
        diagnostics = [x["output"]["result"] for x in invoice["tool_calls"]
                       if x["tool"] == "contract_invoice_diagnostic" and "output" in x]
        require(diagnostics and diagnostics[0]["invoices"][0]["matched_count"] == 2
            and diagnostics[0]["invoices"][0]["flagged_count"] == 2, "Provider invoice tool result is not grounded")
        self.call("hw1-property", "/api/homework/contract/ask", {"message": "Show this scorecard", "conversation_id": result["conversation_id"]}, expected=404)
        for user in ("hw1-general", "hw1-compliance"):
            denied = self.call(user, "/api/homework/contract/ask", {"message": "Show vendor performance and invoices"})
            require(denied["outcome"] == "forbidden" and not denied["tool_calls"], "Agent bypassed prohibited role")
        self.record(REQUIRED[5], "Two genuine configured OpenRouter questions via /api/homework/agent/ask selected Contract Performance: contract_vendor_performance returned53; separate contract_invoice_diagnostic returned matched2/flagged2 with grounded answer. API exposes positive provider-response count and selected model tool names (fallback alone cannot pass). Live manifest SHA256 equals local manifest, all six shared/domain files loaded, typed role tools exclude confirmation. Another principal's conversation=404; prohibited-role agent requests return forbidden with no tools.")


def database_probe():
    """Use the candidate's configured non-superuser connection without extracting secrets."""
    script = r'''
import asyncio, hashlib, json
from sqlalchemy import text
from src.services.compliance_hw1.store import runtime_store
async def main():
    store=runtime_store()
    async with store.engine.connect() as conn:
        role=(await conn.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"))).first()
        assert role and not role[0] and not role[1]
        await conn.execute(text("SELECT set_config('hw1.org','hw1-org-a',false)"))
        rows=(await conn.execute(text("SELECT id,data FROM hw1_compliance.sources WHERE org='hw1-org-a' ORDER BY id"))).mappings().all()
        original=hashlib.sha256(json.dumps([dict(r) for r in rows],sort_keys=True,default=str).encode()).hexdigest()
        await conn.rollback()
        denied=0
        for sql in ["UPDATE hw1_compliance.sources SET data=data WHERE id='hw1-contract-source-a1-02-v3'",
                    "DELETE FROM hw1_compliance.sources WHERE id='hw1-contract-source-a1-02-v3'",
                    "UPDATE hw1_compliance.records SET data=data WHERE id='hw1-wo-report-a1-101-v3'"]:
            await conn.execute(text("SELECT set_config('hw1.org','hw1-org-a',true)"))
            try:
                await conn.execute(text(sql))
            except Exception as error:
                assert getattr(error.orig, 'sqlstate', None) in {'42501','P0001'}
                denied+=1
            finally:
                await conn.rollback()
        assert denied==3
    await store.engine.dispose()
    print(json.dumps({'source_fingerprint':original,'denied_mutations':denied,'least_privilege':True}))
asyncio.run(main())
'''
    result = subprocess.run(["docker", "exec", "-i", "hoistra-hw1-compliance_candidate-1", "python", "-"],
        input=script, capture_output=True, text=True, timeout=60)
    require(result.returncode == 0, "Candidate least-privilege/source-immutability database probe failed")
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise GateFailure("Candidate database probe returned invalid safe metadata") from None


def main():
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"milestone_id": "01-contract", "specification_version": "0.37.0",
        "api_base": BASE, "recorded_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    gate = None
    original = None
    failed_id = REQUIRED[0]
    try:
        gate = Gate()
        before = database_probe()
        original = gate.config()["parameters"]
        # Reproducible expected arithmetic needs explicit defaults. Establish them through
        # the same preview/confirmation API, then restore the pre-gate values at the end.
        defaults = {"weights":{"sla_response_pct":25,"sla_completion_pct":25,"first_fix_pct":20,"recall_pct":15,"accreditation_pct":15},
            "score_cap":60,"recall_window_days":30,"ppm_tolerance_days":7,
            "cost_variance_alert_pct":"15","cost_variance_alert_jobs":3,"invoice_flag_adversary_gbp":"500"}
        if original != defaults:
            gate.confirm(gate.preview(defaults))
        for identifier, run in zip(REQUIRED, [gate.source_and_scope, gate.arithmetic,
                gate.configuration, gate.invoices, gate.denials, gate.agent]):
            failed_id = identifier
            run()
        after = database_probe()
        require(before == after, "Immutable source fingerprints changed during live workflows")
        if gate.config()["parameters"] != original:
            gate.confirm(gate.preview(original))
        gate.checks[0]["evidence"] += " Non-superuser/no-BYPASSRLS DB probe denied original-source UPDATE/DELETE and source-report UPDATE; aggregate source fingerprint unchanged before/after every live workflow."
        receipt["checks"] = gate.checks
        require([r["id"] for r in gate.checks] == REQUIRED, "Missing required acceptance identifier")
        DESTINATION.write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({"milestone_id":"01-contract","passed":True,"checks":len(gate.checks),
                          "receipt":str(DESTINATION.relative_to(ROOT))}))
        return 0
    except Exception as error:
        receipt["checks"] = gate.checks if gate else []
        # Unexpected exceptions are represented by type only; never dump provider/DB bodies.
        safe = str(error) if isinstance(error, GateFailure) else type(error).__name__
        if gate is not None and original is not None:
            try:
                if gate.config()["parameters"] != original:
                    gate.confirm(gate.preview(original))
            except Exception:
                safe += "; original fictional configuration could not be restored"
        receipt["checks"].append({"id":failed_id,"passed":False,"evidence":safe})
        DESTINATION.write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({"milestone_id":"01-contract","passed":False,"failed_check":failed_id,"reason":safe}))
        return 1


if __name__ == "__main__":
    sys.exit(main())

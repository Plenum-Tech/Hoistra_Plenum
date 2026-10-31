#!/usr/bin/env python3
"""Restore only the saved isolated orchestrator; leave the append-only DB untouched."""
import json
from readiness import STATE,rollback,ReadinessFailure
try:
 if STATE.stat().st_mode & 0o077:raise ReadinessFailure('Private rollback state permissions too broad')
 state=json.loads(STATE.read_text())
 if state.get('project')!='hoistra-hw1':raise ReadinessFailure('Wrong rollback project')
 rollback(state)
 print(json.dumps({'rollback':'saved isolated orchestrator restored','database':'preserved; no evidence deletion','port':18008}))
except (ReadinessFailure,OSError,ValueError):raise SystemExit('Rollback failed; inspect private readiness state without exposing credentials') from None

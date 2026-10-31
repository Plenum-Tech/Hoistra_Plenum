"""Common function and disclosure checks; model text never confers authority."""
from fastapi import HTTPException
from .contracts import MANAGERS, REVIEWERS, ROLES

READ_TOOLS = {'compliance_resolve_entity','compliance_list_certificates','compliance_count_certificates',
              'compliance_group_vendors','compliance_get_taxonomy','compliance_get_coverage',
              'compliance_get_evidence','compliance_get_vendor_eligibility','compliance_get_findings',
              'compliance_prepare_proposal','compliance_get_proposal','homework_get_hoi','homework_get_outbox'}


def buildings(principal, selected=None):
    selected = list(principal.building_ids) if selected is None else selected
    if not selected or any(b not in principal.building_ids for b in selected):
        raise HTTPException(403, 'Building access denied')
    return sorted(set(selected))


def authorize_tool(principal, tool):
    if principal.role not in ROLES or tool not in READ_TOOLS:
        raise HTTPException(403, 'Operation not permitted')
    if principal.role=='general_user' and tool=='compliance_get_findings':
        raise HTTPException(403, 'Detailed assessment findings not granted')
    if tool=='homework_get_hoi' and principal.role not in MANAGERS:
        raise HTTPException(403, 'Operation not permitted')
    if tool=='compliance_get_evidence' and principal.role not in REVIEWERS:
        raise HTTPException(403, 'Operation not permitted')


def authorize_row(principal, row, full=False, selected=None):
    scope = set(buildings(principal, selected))
    covered = set(row['buildings'])
    if row['org']!=principal.organization_id or not covered or not covered & scope or (full and not covered <= scope):
        raise HTTPException(404, 'Record unavailable')
    if row['data'].get('sensitive') or not row['data'].get('scope_verified',False):
        raise HTTPException(404, 'Record unavailable')


def authorize_action(principal, action, row):
    authorize_row(principal,row,full=action not in {'email','block_vendor','unblock_vendor'})
    if action=='email':
        if principal.role=='compliance_officer' and row['kind']!='certificate':
            raise HTTPException(403,'Certificate communication target required')
        if principal.role=='general_user' and row['kind'] not in {'building','certificate','asset_summary','work_order_report'}:
            raise HTTPException(403,'Permitted summary target required')
        return
    if principal.role not in REVIEWERS:
        raise HTTPException(403,'Business action not permitted')
    # All supported business mutations here are certificate functions; no generic CRUD.
    if action not in {'accept_certificate','correct_certificate','select_version','request_renewal',
                       'approve_booking','block_vendor','unblock_vendor'}:
        raise HTTPException(403,'Action not permitted')


def allowed_tools(principal):
    return [t for t in sorted(READ_TOOLS)
            if not (t=='homework_get_hoi' and principal.role not in MANAGERS)
            and not (t=='compliance_get_findings' and principal.role=='general_user')
            and not (t=='compliance_get_evidence' and principal.role not in REVIEWERS)]

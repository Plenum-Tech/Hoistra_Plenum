"""Typed model/API contracts prevent identity and arbitrary-query overrides."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

MANAGERS = {'property_manager', 'portfolio_manager'}
REVIEWERS = MANAGERS | {'compliance_officer'}
ROLES = REVIEWERS | {'general_user'}
ACTIONS = Literal['accept_certificate', 'correct_certificate', 'select_version',
                  'request_renewal', 'approve_booking', 'block_vendor', 'unblock_vendor', 'email']

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Query(Contract):
    building_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    entity_id: str | None = Field(default=None, max_length=160)
    identifier: str | None = Field(default=None, max_length=160)
    subject: Literal['Building', 'Vendor'] | None = None
    certificate_type: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=16)
    trade: str | None = Field(default=None, max_length=80)
    expiry_label: Literal['action-required', 'critical', 'expires today', 'expired/lapsed', 'unknown', 'advance reminder', 'current'] | None = None
    days_min: int | None = Field(default=None, ge=-36500, le=36500)
    days_max: int | None = Field(default=None, ge=-36500, le=36500)
    assessment: Literal['pass', 'review', 'fail', 'unassessed'] | None = None
    blocked: bool | None = None
    draft: bool | None = None
    comparison: Literal['gt', 'gte', 'eq'] = 'gt'
    threshold: int = Field(default=2, ge=0, le=10000)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1000000)
    after_id: str | None = Field(default=None, min_length=1, max_length=160)
    include_issues: bool = True

    @model_validator(mode='after')
    def valid_range(self):
        if self.after_id is not None and self.offset:raise ValueError('Use cursor or offset, not both')
        if self.days_min is not None and self.days_max is not None and self.days_min > self.days_max:
            raise ValueError('days_min must not exceed days_max')
        return self

class CertificateValues(Contract):
    reference: str = Field(min_length=1, max_length=120)
    certificate_type: str = Field(min_length=1, max_length=80)
    subject: Literal['Building', 'Vendor']
    holder_id: str = Field(min_length=1, max_length=160)
    issuer: str = Field(min_length=1, max_length=160)
    issue_date: date
    expiry_date: date | None = None
    review_date: date | None = None
    review_rule: str | None = Field(default=None, max_length=200)
    country: str = Field(default='TEST', max_length=16)
    trade: str = Field(default='Fire', max_length=80)

    @model_validator(mode='after')
    def dates_consistent(self):
        if self.expiry_date and self.issue_date > self.expiry_date:
            raise ValueError('Issue date cannot follow expiry')
        if self.review_date and not self.review_rule:
            raise ValueError('Review-date fallback needs a documented rule')
        return self

class Mail(Contract):
    purpose: Literal['certificate_renewal', 'inspection_booking', 'team_notification']
    contact_ids: list[str] = Field(min_length=1, max_length=20)
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=12000)
    source_ids: list[str] = Field(default_factory=list, max_length=20)

class ProposalInput(Contract):
    action: ACTIONS
    entity_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=3, max_length=1000)
    values: CertificateValues | None = None
    source_id: str | None = Field(default=None, max_length=160)
    source_text: str | None = Field(default=None, min_length=1, max_length=20000)
    mail: Mail | None = None
    blocking_reason_ids: list[str] = Field(default_factory=list, max_length=20)

class Confirmation(Contract):
    version: int = Field(ge=1)
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key: str = Field(min_length=8, max_length=160)
    decision: Literal['approve', 'reject'] = 'approve'

class ToolRequest(Contract):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)

class Ask(Contract):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=160)

# Each operation advertises exactly the filters it implements; never silently ignore one.
CERTIFICATE_QUERY_KEYS={'building_ids','entity_id','identifier','subject','certificate_type','country','trade','expiry_label','days_min','days_max','assessment','blocked','draft','limit','offset'}
QUERY_KEYS={
 'compliance_list_certificates':CERTIFICATE_QUERY_KEYS,
 'compliance_count_certificates':CERTIFICATE_QUERY_KEYS-{'limit','offset'},
 'compliance_get_findings':CERTIFICATE_QUERY_KEYS,
 'compliance_group_vendors':CERTIFICATE_QUERY_KEYS|{'comparison','threshold'},
 'compliance_resolve_entity':{'building_ids','identifier','limit','offset'},
 'compliance_get_taxonomy':{'building_ids','subject','country','trade','certificate_type'},
 'compliance_get_coverage':{'building_ids','entity_id','subject','country','trade','certificate_type'},
 'compliance_get_evidence':{'building_ids','entity_id'},
 'compliance_get_vendor_eligibility':{'building_ids','entity_id'},
 'compliance_get_proposal':{'entity_id'},
 'homework_get_hoi':{'building_ids','entity_id'},
 'homework_get_outbox':{'building_ids','limit','offset'},
}

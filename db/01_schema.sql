--
-- PostgreSQL database dump
--

\restrict Jy6KNguMQmLjMUDkAYrtw4sEfyJdLBjngqIaEcH0YpbdEayH4UZllm615C8Cu1e

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: plenum_cafm; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA plenum_cafm;


--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: building_primary_use; Type: TYPE; Schema: plenum_cafm; Owner: -
--

CREATE TYPE plenum_cafm.building_primary_use AS ENUM (
    'Commercial',
    'Residential',
    'Retail',
    'Mixed',
    'Industrial',
    'Logistics',
    'Hospital',
    'Hotel',
    'Education',
    'Laboratory',
    'Leisure',
    'Other'
);


--
-- Name: forbid_ops_audit_mutation(); Type: FUNCTION; Schema: plenum_cafm; Owner: -
--

CREATE FUNCTION plenum_cafm.forbid_ops_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION
        'plenum_cafm.ops_audit_log is append-only: % is not permitted',
        TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
    RETURN NULL;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: activity_log_action; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.activity_log_action (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    entry_id uuid NOT NULL,
    title character varying(255),
    body text,
    options jsonb,
    resolution text,
    status character varying(40),
    blocks_udr boolean DEFAULT false,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone
);


--
-- Name: activity_log_entry; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.activity_log_entry (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    session_id character varying(200),
    trigger_type character varying(40),
    outcome text,
    status character varying(40),
    udr_script_id uuid,
    read_flag boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: activity_log_refinement; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.activity_log_refinement (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    entry_id uuid NOT NULL,
    kind character varying(20),
    title character varying(255),
    body text,
    dismissed boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: activity_log_step; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.activity_log_step (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    entry_id uuid NOT NULL,
    step_index integer,
    kind character varying(20),
    label character varying(255),
    text text,
    confidence numeric(5,2),
    agent_from character varying(120),
    agent_to character varying(120),
    faq_key character varying(120),
    payload jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_audit_log; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.agent_audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_id character varying(50) NOT NULL,
    domain character varying(50) NOT NULL,
    asset_code character varying(50),
    bound_validation_passed boolean DEFAULT false NOT NULL,
    run_1_output jsonb,
    run_2_output jsonb,
    run_3_output jsonb,
    run_1_valid boolean,
    run_2_valid boolean,
    run_3_valid boolean,
    runs_agreed integer DEFAULT 0 NOT NULL,
    winner_status character varying(50),
    winner_confidence numeric(4,3),
    hard_rules_fired jsonb,
    final_status character varying(50),
    confidence_gate_passed boolean DEFAULT false NOT NULL,
    requires_human_review boolean DEFAULT false NOT NULL,
    model_used character varying(50),
    tokens_total integer DEFAULT 0 NOT NULL,
    cost_usd numeric(10,6) DEFAULT 0 NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: approval_action_tokens; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.approval_action_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    queue_item_id uuid NOT NULL,
    token character varying(64) NOT NULL,
    action character varying(40) DEFAULT 'approve'::character varying NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: approvals_queue_items; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.approvals_queue_items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    source_feature character(1) NOT NULL,
    item_type character varying(80) NOT NULL,
    summary text NOT NULL,
    severity character varying(40) NOT NULL,
    status character varying(40) DEFAULT 'pending'::character varying NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    related_entity_type character varying(80),
    related_entity_id uuid,
    email_draft jsonb,
    pm_notes text,
    decided_by uuid,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: asset_categories; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.asset_categories (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(150) NOT NULL,
    description text,
    parent_id uuid
);


--
-- Name: asset_condition_scores; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.asset_condition_scores (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    asset_id uuid NOT NULL,
    asset_code character varying(120),
    condition_score integer NOT NULL,
    llm_label character varying(80),
    provenance_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_report_ref text,
    deduced_at timestamp with time zone DEFAULT now() NOT NULL,
    written_to_asset boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT asset_condition_scores_condition_score_check CHECK (((condition_score >= 1) AND (condition_score <= 5)))
);


--
-- Name: asset_criticality; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.asset_criticality (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    asset_id uuid NOT NULL,
    asset_code character varying(120),
    criticality character varying(8) DEFAULT 'L2'::character varying NOT NULL,
    proposed_criticality character varying(8),
    source character varying(40) DEFAULT 'system'::character varying NOT NULL,
    rationale text,
    approved boolean DEFAULT false NOT NULL,
    approved_by uuid,
    approved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: asset_documents; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.asset_documents (
    id uuid NOT NULL,
    asset_id uuid NOT NULL,
    file_url text NOT NULL,
    document_type character varying(100),
    uploaded_by uuid,
    uploaded_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: asset_offline_log; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.asset_offline_log (
    id uuid NOT NULL,
    asset_id uuid NOT NULL,
    offline_reason character varying(255),
    online_reason character varying(255),
    went_offline_at timestamp without time zone NOT NULL,
    came_online_at timestamp without time zone,
    work_order_id uuid,
    recorded_by uuid,
    notes text
);


--
-- Name: asset_readings; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.asset_readings (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    reading_type character varying(100) NOT NULL,
    value numeric(18,4) NOT NULL,
    unit character varying(50),
    unit_id uuid,
    submitted_by uuid,
    recorded_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: asset_types; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.asset_types (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid,
    asset_type character varying(160) NOT NULL,
    category character varying(160),
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: asset_warranties; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.asset_warranties (
    id uuid NOT NULL,
    asset_id uuid NOT NULL,
    description text,
    provider character varying(255),
    start_date date,
    expiry_date date,
    coverage_notes text,
    document_url text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: assets; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.assets (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    location_id uuid,
    category_id uuid,
    asset_name character varying(255) NOT NULL,
    asset_code character varying(150),
    serial_number character varying(150),
    barcode character varying(255),
    inventory_code character varying(150),
    manufacturer character varying(150),
    make character varying(150),
    model_number character varying(150),
    model character varying(150),
    installation_date date,
    warranty_expiry date,
    status character varying(50) DEFAULT 'active'::character varying NOT NULL,
    is_online boolean DEFAULT true NOT NULL,
    is_site boolean DEFAULT false NOT NULL,
    criticality character varying(50),
    health_score integer,
    aisle character varying(100),
    "row" character varying(100),
    bin_number character varying(100),
    stock_location character varying(255),
    parent_asset_id uuid,
    charge_department_id uuid,
    project_id uuid,
    account_code character varying(100),
    notes text,
    qr_code character varying(255),
    timezone character varying(100),
    raw_metadata jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    condition_score integer,
    condition_provenance jsonb DEFAULT '{}'::jsonb,
    condition_updated_at timestamp with time zone,
    building_id uuid,
    site_id uuid,
    asset_type_id uuid,
    manufacturer_id uuid,
    space_id uuid,
    CONSTRAINT chk_assets_health_score CHECK (((health_score IS NULL) OR ((health_score >= 0) AND (health_score <= 100))))
);


--
-- Name: audit_logs; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.audit_logs (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    user_id uuid,
    action character varying(150) NOT NULL,
    entity_type character varying(100),
    entity_id uuid,
    metadata text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: auth_otp_codes; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.auth_otp_codes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid,
    email text NOT NULL,
    purpose text NOT NULL,
    code_hash text NOT NULL,
    salt text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    consumed_at timestamp with time zone,
    invalidated_at timestamp with time zone,
    request_ip text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_auth_otp_purpose CHECK ((purpose = ANY (ARRAY['email_verification'::text, 'password_reset'::text])))
);


--
-- Name: auth_role_changes; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.auth_role_changes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    changed_by uuid,
    from_role character varying(20),
    to_role character varying(20) NOT NULL,
    reason text,
    request_ip text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: auth_sessions; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.auth_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    refresh_token_hash text NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    revoked_reason text,
    user_agent text,
    request_ip text,
    last_used_at timestamp with time zone
);


--
-- Name: bom_group_parts; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.bom_group_parts (
    id uuid NOT NULL,
    bom_group_id uuid NOT NULL,
    part_id uuid NOT NULL,
    quantity integer DEFAULT 1 NOT NULL
);


--
-- Name: bom_groups; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.bom_groups (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: building_country_packs; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.building_country_packs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    site_id uuid NOT NULL,
    country_code character varying(8) DEFAULT 'UK'::character varying NOT NULL,
    pack_version character varying(40) DEFAULT '1.1'::character varying NOT NULL,
    activated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: building_energy_profiles; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.building_energy_profiles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    site_id uuid NOT NULL,
    building_type character varying(80) DEFAULT 'office'::character varying NOT NULL,
    gia_m2 numeric(14,2) NOT NULL,
    tm46_electricity_benchmark numeric(14,4),
    tm46_gas_benchmark numeric(14,4),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: buildings; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.buildings (
    building_id uuid DEFAULT gen_random_uuid() NOT NULL,
    site_id text,
    location_id uuid,
    name text,
    primary_use plenum_cafm.building_primary_use,
    floors integer,
    gross_area_sqft numeric(16,2),
    eui_kwh_m2 numeric(14,2),
    building_code character varying(50),
    hoist_score integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: charge_departments; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.charge_departments (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    code character varying(100) NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: claude_api_usage; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.claude_api_usage (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ingestion_id uuid,
    query_id uuid,
    service character varying(60) NOT NULL,
    agent_id character varying(50),
    model character varying(50) NOT NULL,
    tokens_in integer DEFAULT 0 NOT NULL,
    tokens_out integer DEFAULT 0 NOT NULL,
    cache_read_tokens integer DEFAULT 0 NOT NULL,
    cost_usd numeric(10,6) DEFAULT 0 NOT NULL,
    latency_ms integer DEFAULT 0 NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: claude_budget_config; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.claude_budget_config (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    period character varying(20) DEFAULT 'monthly'::character varying NOT NULL,
    limit_usd numeric(10,2) NOT NULL,
    alert_threshold_pct numeric(5,2) DEFAULT 80.0 NOT NULL,
    auto_pause boolean DEFAULT true NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: compliance_certificates; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.compliance_certificates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid,
    certificate_ref character varying(160),
    cert_type character varying(80),
    asset_id uuid,
    site_id uuid,
    duty_holder_id uuid,
    issuer character varying(255),
    issue_date date,
    expiry_date date,
    status character varying(40),
    source_document_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    organization_id uuid,
    vendor_id uuid,
    certificate_type_code character varying(80),
    certificate_number character varying(160),
    next_due_date date,
    inspection_frequency_months integer,
    inspector_name character varying(255),
    inspector_accreditation_number character varying(160),
    result character varying(40),
    defects_found text,
    remedial_actions text,
    remedial_status character varying(40) DEFAULT 'Closed'::character varying,
    document_id uuid,
    cert_scope character varying(20),
    country_code character varying(8) DEFAULT 'UK'::character varying,
    authenticity_warning text,
    days_to_expiry integer,
    insurance_risk_flag boolean DEFAULT false,
    raw_metadata jsonb DEFAULT '{}'::jsonb,
    building_id uuid,
    building_name character varying(255),
    building_reference character varying(120),
    region character varying(64),
    state character varying(64),
    site_ref character varying(120)
);


--
-- Name: compliance_risk_snapshots; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.compliance_risk_snapshots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    snapshot_date date NOT NULL,
    vendors_blocked integer DEFAULT 0 NOT NULL,
    high_risk_lt_30 integer DEFAULT 0 NOT NULL,
    medium_risk_lt_90 integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: compliance_scan_runs; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.compliance_scan_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    scope character varying(80) DEFAULT 'all'::character varying NOT NULL,
    scope_filter jsonb DEFAULT '{}'::jsonb NOT NULL,
    building_scanned integer DEFAULT 0 NOT NULL,
    vendor_scanned integer DEFAULT 0 NOT NULL,
    alerts_created integer DEFAULT 0 NOT NULL,
    blocks_set integer DEFAULT 0 NOT NULL,
    adversary_passed integer DEFAULT 0 NOT NULL,
    adversary_failed integer DEFAULT 0 NOT NULL,
    status character varying(40) DEFAULT 'completed'::character varying NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: compliance_vector_membership_audit; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.compliance_vector_membership_audit (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    action character varying(20) NOT NULL,
    actor character varying(120),
    organization_id uuid,
    detail jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: compliance_verification_register_rows; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.compliance_verification_register_rows (
    id uuid NOT NULL,
    certificate_type_code character varying(80) NOT NULL,
    lookup_key character varying(255) NOT NULL,
    payload jsonb,
    source_file character varying(255),
    ingested_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: compliance_verification_sources; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.compliance_verification_sources (
    certificate_type_code character varying(80) NOT NULL,
    cert_scope character varying(20) NOT NULL,
    verification_group character varying(40) NOT NULL,
    channel character varying(20) NOT NULL,
    register character varying(120),
    source_url text,
    api_available character varying(12) NOT NULL,
    refresh_cadence character varying(20),
    notes text,
    code_aliases jsonb DEFAULT '[]'::jsonb NOT NULL,
    country_code character varying(8) DEFAULT 'UK'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: contract_documents; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.contract_documents (
    id uuid NOT NULL,
    organization_id uuid,
    vendor_id uuid,
    contract_id uuid,
    document_id uuid NOT NULL,
    document_kind character varying(40) NOT NULL,
    document_name character varying(400),
    blob_url text,
    relationship character varying(40) NOT NULL,
    confidence numeric(4,3),
    extraction_status character varying(30) NOT NULL,
    extracted_fields jsonb NOT NULL,
    vector_chunk_ids jsonb NOT NULL,
    linked_by uuid,
    linked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: contract_sla_parameters; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.contract_sla_parameters (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    vendor_id uuid,
    contract_id uuid,
    document_id uuid,
    contract_ref character varying(160),
    status character varying(40) DEFAULT 'draft'::character varying NOT NULL,
    sla_response_p1_hours numeric(10,2),
    sla_response_p2_hours numeric(10,2),
    sla_response_p3_hours numeric(10,2),
    sla_response_p4_hours numeric(10,2),
    sla_completion_p1_hours numeric(10,2),
    sla_completion_p2_hours numeric(10,2),
    sla_completion_p3_hours numeric(10,2),
    sla_completion_p4_hours numeric(10,2),
    labour_day_rate numeric(12,2),
    overtime_rate numeric(12,2),
    call_out_rate numeric(12,2),
    parts_pricing_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    payment_terms text,
    kpi_clauses_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    ppm_obligations_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    task_criticality_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    defaults_used jsonb DEFAULT '[]'::jsonb NOT NULL,
    overrides_log jsonb DEFAULT '[]'::jsonb NOT NULL,
    field_sources jsonb DEFAULT '{}'::jsonb NOT NULL,
    raw_extraction jsonb DEFAULT '{}'::jsonb NOT NULL,
    confirmed_by uuid,
    confirmed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    signed_date date,
    labour_hour_rate numeric(12,2)
);


--
-- Name: documents; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.documents (
    document_id uuid DEFAULT gen_random_uuid() NOT NULL,
    building_id uuid,
    doc_type text,
    title text,
    file_name text,
    blob_url text,
    uploaded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: contracts; Type: VIEW; Schema: plenum_cafm; Owner: -
--

CREATE VIEW plenum_cafm.contracts AS
 SELECT COALESCE(p.contract_id, p.id) AS contract_id,
    p.organization_id,
    d.building_id,
    p.vendor_id,
    p.contract_ref,
    p.document_id,
    p.sla_response_p1_hours AS sla_response_hrs,
    NULLIF((p.kpi_clauses_json)::text, 'null'::text) AS penalty_clause,
    NULL::numeric(16,2) AS annual_value,
    NULL::character(3) AS currency,
    p.signed_date AS start_date,
    NULL::date AS end_date,
    p.status,
    p.created_at
   FROM (plenum_cafm.contract_sla_parameters p
     LEFT JOIN plenum_cafm.documents d ON ((d.document_id = p.document_id)));


--
-- Name: corrections_log; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.corrections_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ingestion_id uuid NOT NULL,
    field_path character varying(255) NOT NULL,
    original_value text,
    corrected_value text,
    correction_type character varying(50) DEFAULT 'wrong_value'::character varying NOT NULL,
    reviewer_id uuid NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cost_variance_alerts; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.cost_variance_alerts (
    id uuid NOT NULL,
    organization_id uuid,
    vendor_id uuid,
    score_month date NOT NULL,
    threshold_pct numeric(5,2) NOT NULL,
    job_count_threshold integer NOT NULL,
    breach_job_count integer NOT NULL,
    scored_job_count integer NOT NULL,
    total_estimated numeric(14,2),
    total_actual numeric(14,2),
    total_delta numeric(14,2),
    avg_variance_pct numeric(8,2),
    max_variance_pct numeric(8,2),
    work_order_ids jsonb NOT NULL,
    status character varying(30) NOT NULL,
    approvals_queue_item_id uuid,
    acknowledged_by uuid,
    acknowledged_at timestamp with time zone,
    detail_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: country_certificate_packs; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.country_certificate_packs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    pack_id character varying(80) NOT NULL,
    country_code character varying(8) NOT NULL,
    pack_version character varying(40) DEFAULT '1.0'::character varying NOT NULL,
    certificate_type_code character varying(80) NOT NULL,
    certificate_type_name character varying(255) NOT NULL,
    certificate_scope character varying(20) NOT NULL,
    trade_category character varying(120),
    regulation_reference character varying(255),
    regulation_url text,
    frequency_months integer,
    issuing_body character varying(255),
    required_contractor_accreditation character varying(255),
    verification_url text,
    key_fields_schema jsonb DEFAULT '{}'::jsonb NOT NULL,
    alert_thresholds jsonb DEFAULT '{"current_gt": 90, "lapsed_lte": 7, "overdue_max": 30, "overdue_min": 8, "expiring_soon_max": 90, "expiring_soon_min": 61, "due_for_renewal_max": 60, "due_for_renewal_min": 31}'::jsonb NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    change_notes text
);


--
-- Name: document_chunks; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.document_chunks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    document_id uuid,
    chunk_index integer,
    content text,
    embedding public.vector(1536),
    source_entity_type character varying(120),
    source_entity_id text,
    document_type character varying(120),
    page_reference character varying(80),
    anchor_is_primary_key boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document_generation_log; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.document_generation_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    request_text text,
    intent_type character varying(30),
    document_type character varying(50),
    document_plan_json jsonb,
    plan_validation_passed boolean,
    output_format character varying(10),
    output_blob_url text,
    data_sources jsonb,
    spot_checks_run integer,
    spot_checks_passed integer,
    eval_score numeric(4,3),
    plan_runs_agreed integer,
    held_for_review boolean DEFAULT false NOT NULL,
    model_used character varying(50),
    tokens_in integer DEFAULT 0 NOT NULL,
    tokens_out integer DEFAULT 0 NOT NULL,
    cost_usd numeric(10,6) DEFAULT 0 NOT NULL,
    render_ms integer,
    user_id uuid,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: duty_holders; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.duty_holders (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    role character varying(160),
    email character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: energy_anomalies; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.energy_anomalies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    site_id uuid,
    meter_id uuid,
    asset_id uuid,
    anomaly_type character varying(60) NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    window_start timestamp with time zone,
    window_end timestamp with time zone,
    metric_pct numeric(10,4),
    excess_kwh numeric(16,4),
    annualised_excess_kwh numeric(16,4),
    financial_gbp numeric(14,2),
    tariff_used numeric(12,6),
    detail_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(40) DEFAULT 'open'::character varying NOT NULL,
    pm_action character varying(40),
    pm_reason text,
    acted_at timestamp with time zone,
    queue_item_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: energy_meters; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.energy_meters (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    site_id uuid,
    asset_id uuid,
    meter_type character varying(20) NOT NULL,
    mpan character varying(40),
    mprn character varying(40),
    dcc_device_id character varying(80),
    tariff_gbp_per_kwh numeric(12,6) DEFAULT 0.28 NOT NULL,
    carbon_kg_per_kwh numeric(12,6) DEFAULT 0.207 NOT NULL,
    is_sub_meter boolean DEFAULT false NOT NULL,
    asset_type_benchmark_kwh numeric(14,4),
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: energy_monthly_reports; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.energy_monthly_reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    site_id uuid,
    report_month date NOT NULL,
    eui_trend_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    anomalies_ranked_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    total_excess_cost_gbp numeric(14,2),
    carbon_exposure_kg numeric(16,4),
    report_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    pdf_blob_url text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: energy_recommendations; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.energy_recommendations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    asset_id uuid,
    site_id uuid,
    recommendation_type character varying(40) NOT NULL,
    condition_score integer,
    consumption_vs_benchmark_pct numeric(10,4),
    repair_cost_gbp numeric(14,2),
    replace_cost_gbp numeric(14,2),
    summary text NOT NULL,
    detail_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(40) DEFAULT 'open'::character varying NOT NULL,
    queue_item_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: equipment; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.equipment (
    equipment_id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id text,
    name text,
    manufacturer text,
    model text,
    serial text,
    installed_on date,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: eui_snapshots; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.eui_snapshots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    site_id uuid NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    meter_type character varying(20) NOT NULL,
    total_kwh numeric(16,4) NOT NULL,
    gia_m2 numeric(14,2) NOT NULL,
    eui_kwh_per_m2 numeric(14,6) NOT NULL,
    benchmark_kwh_per_m2 numeric(14,6),
    deviation_pct numeric(10,4),
    excess_kwh numeric(16,4),
    financial_gbp numeric(14,2),
    tariff_used numeric(12,6),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: files; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.files (
    id uuid NOT NULL,
    organization_id uuid,
    name character varying(500) NOT NULL,
    blob_url text,
    file_size_bytes integer,
    mime_type character varying(150),
    entity_type character varying(100),
    entity_id uuid,
    uploaded_by uuid,
    uploaded_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: floors; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.floors (
    floor_id uuid DEFAULT gen_random_uuid() NOT NULL,
    building_id uuid,
    level integer,
    name text,
    gross_area_sqft numeric(16,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: fm_report_staleness; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.fm_report_staleness (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    vendor_id uuid,
    last_report_at timestamp with time zone,
    data_as_of date,
    note text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ingestion_audit_log; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.ingestion_audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ingestion_id uuid NOT NULL,
    event_type character varying(50) NOT NULL,
    model_used character varying(50),
    prompt_version character varying(20),
    eval_score numeric(4,3),
    rules_violations jsonb,
    reviewer_id uuid,
    decision character varying(20),
    corrected_json jsonb,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ingestion_documents; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.ingestion_documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    source_type character varying(20) NOT NULL,
    agent_id character varying(50) NOT NULL,
    original_filename character varying(500) NOT NULL,
    blob_url text,
    file_hash_sha256 character varying(64),
    page_count integer,
    intermediate_json jsonb,
    final_json jsonb,
    status character varying(20) DEFAULT 'queued'::character varying NOT NULL,
    confidence_overall character varying(10),
    eval_score numeric(4,3),
    model_used character varying(50),
    prompt_template_id uuid,
    tokens_in integer DEFAULT 0 NOT NULL,
    tokens_out integer DEFAULT 0 NOT NULL,
    cache_read_tokens integer DEFAULT 0 NOT NULL,
    cost_usd numeric(10,6) DEFAULT 0 NOT NULL,
    processing_ms integer DEFAULT 0 NOT NULL,
    uploaded_by uuid,
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone
);


--
-- Name: inspections; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.inspections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid,
    asset_code character varying(50),
    ingestion_id uuid,
    inspector character varying(255),
    inspection_date date,
    section character varying(10),
    finding_type character varying(100),
    observations text,
    risk_level character varying(20),
    corrective_action boolean DEFAULT false NOT NULL,
    source_file character varying(500),
    findings_jsonb jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: inventory_transactions; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.inventory_transactions (
    id uuid NOT NULL,
    part_id uuid NOT NULL,
    transaction_type character varying(50) NOT NULL,
    quantity integer NOT NULL,
    unit_cost numeric(18,2),
    total_cost numeric(18,2),
    reference_type character varying(100),
    reference_id uuid,
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: invoice_lines; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.invoice_lines (
    id uuid NOT NULL,
    invoice_verification_id uuid NOT NULL,
    organization_id uuid,
    vendor_id uuid,
    invoice_ref character varying(160),
    line_no integer,
    line_ref character varying(80),
    work_order_id uuid,
    wo_code character varying(120),
    description text,
    labour_hours numeric(10,2),
    labour_rate numeric(12,2),
    labour_amount numeric(14,2),
    part_code character varying(80),
    parts_qty numeric(12,2),
    parts_cost numeric(14,2),
    line_total numeric(14,2),
    reported_labour_hours numeric(10,2),
    contract_labour_rate numeric(12,2),
    contract_parts_cost numeric(14,2),
    match_status character varying(20) NOT NULL,
    discrepancy_code character varying(60),
    discrepancy_text text,
    delta_gbp numeric(14,2),
    adversary_reviewed boolean NOT NULL,
    adversary_agreed boolean,
    adversary_delta_gbp numeric(14,2),
    pm_decision character varying(20),
    pm_decided_by uuid,
    pm_decided_at timestamp with time zone,
    pm_note text,
    raw_metadata jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: invoice_verifications; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.invoice_verifications (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    vendor_id uuid,
    invoice_ref character varying(160),
    document_id uuid,
    status character varying(40) DEFAULT 'pending'::character varying NOT NULL,
    matched_count integer DEFAULT 0 NOT NULL,
    flagged_count integer DEFAULT 0 NOT NULL,
    matched_flagged_ratio numeric(6,4),
    lines_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    insights_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: invoices; Type: VIEW; Schema: plenum_cafm; Owner: -
--

CREATE VIEW plenum_cafm.invoices AS
 SELECT v.id AS invoice_id,
    v.organization_id,
    v.vendor_id,
    d.building_id,
    v.invoice_ref,
    v.document_id,
    l.amount,
    l.line_count,
    NULL::character(3) AS currency,
    NULL::date AS issued_on,
    v.status,
    v.matched_count,
    v.flagged_count,
    v.created_at
   FROM ((plenum_cafm.invoice_verifications v
     LEFT JOIN plenum_cafm.documents d ON ((d.document_id = v.document_id)))
     LEFT JOIN LATERAL ( SELECT sum(il.line_total) AS amount,
            count(*) AS line_count
           FROM plenum_cafm.invoice_lines il
          WHERE (il.invoice_verification_id = v.id)) l ON (true));


--
-- Name: leases; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.leases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    lease_ref character varying(160),
    tenant_id uuid,
    space_id uuid,
    start_date date,
    end_date date,
    rent_amount numeric(16,2),
    currency character varying(8),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: locations; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.locations (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    type character varying(100) NOT NULL,
    parent_location_id uuid,
    level integer,
    address text,
    city character varying(150),
    province character varying(150),
    postal_code character varying(20),
    country character varying(100),
    timezone character varying(100),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    site_id text,
    building_id uuid,
    country_code character(2),
    region text,
    pack_id uuid,
    postcode character varying(40),
    updated_at timestamp with time zone
);


--
-- Name: maintenance_history; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.maintenance_history (
    id uuid NOT NULL,
    asset_id uuid NOT NULL,
    work_order_id uuid,
    performed_by uuid,
    performed_at timestamp without time zone DEFAULT now() NOT NULL,
    notes text
);


--
-- Name: maintenance_plans; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.maintenance_plans (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    asset_id uuid NOT NULL,
    sm_code character varying(150),
    description text,
    maintenance_type character varying(100) NOT NULL,
    maintenance_type_id uuid,
    priority_id uuid,
    task_group_id uuid,
    frequency_type character varying(50) NOT NULL,
    frequency_value integer NOT NULL,
    next_due_date date,
    status character varying(50) DEFAULT 'active'::character varying NOT NULL,
    project_id uuid,
    charge_department_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_maintenance_frequency_value CHECK ((frequency_value > 0))
);


--
-- Name: maintenance_types; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.maintenance_types (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(150) NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: manufacturers; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.manufacturers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid,
    manufacturer_name character varying(200) NOT NULL,
    manufacturer_country character varying(120),
    support_email character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: meter_reading_gaps; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.meter_reading_gaps (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    meter_id uuid NOT NULL,
    gap_start timestamp with time zone NOT NULL,
    gap_end timestamp with time zone NOT NULL,
    missing_periods integer NOT NULL,
    retry_count integer DEFAULT 0 NOT NULL,
    status character varying(40) DEFAULT 'open'::character varying NOT NULL,
    last_retry_at timestamp with time zone,
    escalated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: meter_reading_units; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.meter_reading_units (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(150) NOT NULL,
    symbol character varying(20) NOT NULL,
    "precision" integer DEFAULT 2 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: meter_readings; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.meter_readings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    meter_id uuid NOT NULL,
    asset_id uuid,
    reading_at timestamp with time zone NOT NULL,
    period_minutes integer DEFAULT 30 NOT NULL,
    consumption_kwh numeric(14,6) NOT NULL,
    source character varying(40) DEFAULT 'dcc'::character varying NOT NULL,
    quality_flag character varying(40),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: meters; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.meters (
    meter_id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id text,
    building_id uuid,
    meter_type text,
    mpan_mprn text,
    unit text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: misc_cost_types; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.misc_cost_types (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(150) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: misc_costs; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.misc_costs (
    id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    misc_cost_type_id uuid,
    description text,
    amount numeric(18,2),
    created_by uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: notifications; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.notifications (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    user_id uuid,
    title character varying(255) NOT NULL,
    message text NOT NULL,
    type character varying(100),
    entity_type character varying(100),
    entity_id uuid,
    is_read boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: ops_audit_log; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.ops_audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    actor character varying(160) NOT NULL,
    action_type character varying(120) NOT NULL,
    source_feature character(1),
    input_hash character varying(64),
    output_hash character varying(64),
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ops_email_log; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.ops_email_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    queue_item_id uuid,
    to_address character varying(255) NOT NULL,
    subject character varying(500) NOT NULL,
    body text NOT NULL,
    status character varying(40) DEFAULT 'queued'::character varying NOT NULL,
    error_message text,
    sent_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: orchestration_audit_log; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.orchestration_audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_code character varying(50),
    bound_passed boolean DEFAULT false NOT NULL,
    run_1_valid boolean,
    run_2_valid boolean,
    run_3_valid boolean,
    runs_agreed integer DEFAULT 0 NOT NULL,
    action character varying(50),
    priority character varying(20),
    confidence numeric(4,3),
    reasoning text,
    confidence_gate_passed boolean DEFAULT false NOT NULL,
    safety_passed boolean DEFAULT false NOT NULL,
    agent_results_jsonb jsonb,
    hard_rules_fired jsonb,
    model_used character varying(50),
    tokens_total integer DEFAULT 0 NOT NULL,
    cost_usd numeric(10,6) DEFAULT 0 NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: organizations; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.organizations (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    industry character varying(150),
    address text,
    country character varying(100),
    timezone character varying(100),
    status character varying(50) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: permissions; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.permissions (
    id uuid NOT NULL,
    name character varying(150) NOT NULL,
    description text
);


--
-- Name: pinned_run; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.pinned_run (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid,
    label character varying(160),
    prompt text,
    is_default boolean DEFAULT false,
    tool_context character varying(80),
    usage_count integer DEFAULT 0,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: portfolios; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.portfolios (
    portfolio_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text,
    owner_entity text,
    reporting_currency character(3),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ppm_visits; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.ppm_visits (
    id uuid NOT NULL,
    organization_id uuid,
    vendor_id uuid,
    contract_id uuid,
    asset_id uuid,
    asset_code character varying(120),
    work_order_id uuid,
    wo_code character varying(120),
    ppm_ref character varying(120),
    frequency character varying(40),
    score_month date,
    scheduled_date date NOT NULL,
    completed_date date,
    tolerance_days integer NOT NULL,
    variance_days integer,
    within_tolerance boolean,
    status character varying(40) NOT NULL,
    source character varying(40) NOT NULL,
    source_document_id uuid,
    raw_metadata jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: priorities; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.priorities (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    color_hex character varying(10),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: projects; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.projects (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    site_id uuid,
    status character varying(50) DEFAULT 'active'::character varying NOT NULL,
    start_date date,
    end_date date,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: prompt_ab_tests; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.prompt_ab_tests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    template_a_id uuid NOT NULL,
    template_b_id uuid NOT NULL,
    status character varying(20) DEFAULT 'running'::character varying NOT NULL,
    accuracy_a numeric(5,4),
    accuracy_b numeric(5,4),
    winner_id uuid,
    docs_processed integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);


--
-- Name: prompt_templates; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.prompt_templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_id character varying(50) NOT NULL,
    doc_type character varying(50) NOT NULL,
    system_prompt text NOT NULL,
    user_template text NOT NULL,
    extraction_schema jsonb,
    version character varying(20) DEFAULT '1.0'::character varying NOT NULL,
    accuracy_score numeric(5,4),
    usage_count integer DEFAULT 0 NOT NULL,
    avg_tokens integer,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: purchase_order_line_items; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.purchase_order_line_items (
    id uuid NOT NULL,
    purchase_order_id uuid NOT NULL,
    part_id uuid,
    asset_id uuid,
    description text,
    quantity integer DEFAULT 1 NOT NULL,
    unit_price numeric(18,2),
    total_price numeric(18,2),
    tax_rate numeric(6,4)
);


--
-- Name: purchase_order_statuses; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.purchase_order_statuses (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    is_closed_state boolean DEFAULT false NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: purchase_orders; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.purchase_orders (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    po_code character varying(150),
    supplier_id uuid,
    status_id uuid,
    status character varying(50) DEFAULT 'draft'::character varying NOT NULL,
    site_id uuid,
    charge_department_id uuid,
    subtotal numeric(18,2),
    tax_amount numeric(18,2),
    total_amount numeric(18,2),
    notes text,
    created_by uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    received_at timestamp without time zone,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: query_audit_log; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.query_audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    query_text text NOT NULL,
    intent_classified character varying(30),
    retrieval_tier character varying(10),
    docs_consulted jsonb,
    model_used character varying(50),
    response_text text,
    eval_score numeric(4,3),
    output_format character varying(10),
    tokens_in integer DEFAULT 0 NOT NULL,
    tokens_out integer DEFAULT 0 NOT NULL,
    cost_usd numeric(10,6) DEFAULT 0 NOT NULL,
    latency_ms integer DEFAULT 0 NOT NULL,
    user_id uuid,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rca_actions; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.rca_actions (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    description text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: rca_causes; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.rca_causes (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    description text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: rca_grouping_actions; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.rca_grouping_actions (
    id uuid NOT NULL,
    rca_grouping_id uuid NOT NULL,
    rca_action_id uuid NOT NULL,
    assigned_to uuid,
    due_date date,
    completed_at timestamp without time zone
);


--
-- Name: rca_grouping_causes; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.rca_grouping_causes (
    id uuid NOT NULL,
    rca_grouping_id uuid NOT NULL,
    rca_cause_id uuid NOT NULL
);


--
-- Name: rca_groupings; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.rca_groupings (
    id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    problem_id uuid,
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: rca_problems; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.rca_problems (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    description text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: receipt_line_items; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.receipt_line_items (
    id uuid NOT NULL,
    receipt_id uuid NOT NULL,
    po_line_item_id uuid,
    part_id uuid,
    quantity_ordered integer,
    quantity_received integer DEFAULT 0 NOT NULL,
    notes text
);


--
-- Name: receipts; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.receipts (
    id uuid NOT NULL,
    purchase_order_id uuid NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    site_id uuid,
    received_by uuid,
    received_at timestamp without time zone,
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: regulation_packs; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.regulation_packs (
    pack_id uuid DEFAULT gen_random_uuid() NOT NULL,
    standard text,
    required_types text[],
    benchmark_source text,
    standing character varying(40),
    standing_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: resource_skills; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.resource_skills (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    vendor_id uuid NOT NULL,
    resource_id uuid,
    operative_name character varying(255),
    skill_type_code character varying(80) NOT NULL,
    certificate_number character varying(160),
    issue_date date,
    expiry_date date,
    status character varying(40),
    days_to_expiry integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: resources; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.resources (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    resource_code character varying(120),
    resource_name character varying(255),
    skill_type character varying(160),
    vendor_id uuid,
    contract_id uuid,
    cost_rate numeric(12,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: review_queue; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.review_queue (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ingestion_id uuid NOT NULL,
    field_path character varying(255),
    extracted_value text,
    confidence character varying(10),
    routing_reason character varying(255),
    reviewer_id uuid,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    decision character varying(20),
    corrected_value text,
    locked_until timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_at timestamp with time zone
);


--
-- Name: role_permissions; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.role_permissions (
    id uuid NOT NULL,
    role_id uuid NOT NULL,
    permission_id uuid NOT NULL
);


--
-- Name: roles; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.roles (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    description text
);


--
-- Name: saved_space; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.saved_space (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    name character varying(200) NOT NULL,
    kind character varying(40),
    standard_key character varying(80),
    semantic_tag character varying(200),
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: saved_space_item; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.saved_space_item (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    item_kind character varying(40),
    item_ref character varying(200),
    session_id character varying(200),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: schedule_triggers; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.schedule_triggers (
    id uuid NOT NULL,
    maintenance_plan_id uuid NOT NULL,
    trigger_type character varying(20) NOT NULL,
    interval_value integer,
    interval_unit character varying(20),
    meter_interval numeric(18,4),
    meter_unit_id uuid,
    last_triggered_at timestamp without time zone,
    next_due_date date,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: scheduled_maintenance_assets; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.scheduled_maintenance_assets (
    id uuid NOT NULL,
    maintenance_plan_id uuid NOT NULL,
    asset_id uuid NOT NULL
);


--
-- Name: scheduled_maintenance_parts; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.scheduled_maintenance_parts (
    id uuid NOT NULL,
    maintenance_plan_id uuid NOT NULL,
    part_id uuid NOT NULL,
    quantity_required integer DEFAULT 1 NOT NULL
);


--
-- Name: scheduled_maintenance_users; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.scheduled_maintenance_users (
    id uuid NOT NULL,
    maintenance_plan_id uuid NOT NULL,
    user_id uuid NOT NULL
);


--
-- Name: scheduled_tasks; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.scheduled_tasks (
    id uuid NOT NULL,
    task_group_id uuid,
    maintenance_plan_id uuid,
    description text NOT NULL,
    estimated_hours numeric(10,2),
    task_type integer,
    sort_order integer DEFAULT 0 NOT NULL,
    asset_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: site_occupancy_logs; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.site_occupancy_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    site_id uuid NOT NULL,
    occupancy_state character varying(40) NOT NULL,
    changed_at timestamp with time zone NOT NULL,
    notes text,
    source character varying(40) DEFAULT 'manual'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sites; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.sites (
    site_id character varying(50) NOT NULL,
    organization_id integer,
    site_name character varying(200) NOT NULL,
    site_code character varying(50),
    city character varying(100),
    country character varying(100),
    timezone character varying(50) DEFAULT 'Asia/Dubai'::character varying,
    status character varying(50) DEFAULT 'active'::character varying,
    created_at timestamp without time zone DEFAULT now(),
    site_type text,
    floors text,
    gfa_sqm text,
    occupancy_profile text,
    id text,
    completed_at text,
    handover_date text,
    postcode text,
    name text,
    asset_id text,
    manager text,
    region text,
    manager_email text,
    building_name character varying(255),
    building_code character varying(50),
    country_code character varying(8),
    state character varying(120),
    use_type character varying(80),
    use_mix jsonb,
    metering_route character varying(160),
    metering_granularity character varying(40),
    benchmark_standard character varying(120),
    benchmark_standing character varying(40),
    benchmark_standing_note text,
    eui_kwh_per_m2 numeric(14,2),
    benchmark_kwh_per_m2 numeric(14,2),
    hoist_score integer,
    portfolio_id uuid,
    address text
);


--
-- Name: sla_policies; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.sla_policies (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(150) NOT NULL,
    priority character varying(50) NOT NULL,
    response_time_minutes integer NOT NULL,
    resolution_time_minutes integer NOT NULL,
    CONSTRAINT chk_sla_resolution_time CHECK ((resolution_time_minutes >= 0)),
    CONSTRAINT chk_sla_response_time CHECK ((response_time_minutes >= 0))
);


--
-- Name: spaces; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.spaces (
    space_id uuid DEFAULT gen_random_uuid() NOT NULL,
    floor_id uuid,
    building_id uuid,
    name text,
    space_type text,
    gross_area_sqft numeric(16,2),
    tenant_ref text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: spare_parts; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.spare_parts (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    part_name character varying(255) NOT NULL,
    part_code character varying(150),
    description text,
    unit_price numeric(18,2),
    stock_quantity integer DEFAULT 0 NOT NULL,
    reorder_level integer DEFAULT 0 NOT NULL,
    max_quantity integer,
    unit_of_measure character varying(50),
    aisle character varying(100),
    "row" character varying(100),
    bin_number character varying(100),
    supplier_id uuid,
    bom_group_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    manufacturer_id uuid,
    CONSTRAINT chk_spare_parts_reorder_level CHECK ((reorder_level >= 0)),
    CONSTRAINT chk_spare_parts_stock_quantity CHECK ((stock_quantity >= 0))
);


--
-- Name: task_groups; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.task_groups (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: technician_skills; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.technician_skills (
    id uuid NOT NULL,
    technician_id uuid NOT NULL,
    skill_name character varying(150) NOT NULL,
    skill_level character varying(50)
);


--
-- Name: technicians; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.technicians (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    user_id uuid NOT NULL,
    base_location character varying(255),
    availability_status character varying(50),
    performance_score numeric(10,2),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: tenants; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.tenants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    tenant_code character varying(120),
    tenant_name character varying(255),
    contact_email character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: udr_canonical_column; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.udr_canonical_column (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    canonical_table character varying(160) NOT NULL,
    canonical_column character varying(160) NOT NULL,
    datatype character varying(80),
    classification character varying(40),
    cell_value_format character varying(120),
    sample_values jsonb,
    references_table character varying(160),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: udr_canonical_table; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.udr_canonical_table (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    canonical_table character varying(160) NOT NULL,
    hierarchy_level character varying(80),
    description text,
    primary_key_column character varying(160),
    standard_columns jsonb,
    sample_values jsonb,
    is_reference_table boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: udr_mapping_decision; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.udr_mapping_decision (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    udr_script_id uuid NOT NULL,
    decision_kind character varying(20),
    source_table character varying(200),
    source_column character varying(200),
    dest_table character varying(200),
    dest_column character varying(200),
    confidence numeric(5,2),
    resolved_by character varying(40),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: udr_ontology_chunk; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.udr_ontology_chunk (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    chunk_key character varying(200) NOT NULL,
    concept character varying(80),
    entity character varying(160),
    intent_tags jsonb,
    content text,
    embedding public.vector(1536),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: udr_relationship; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.udr_relationship (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    udr_script_id uuid,
    from_table character varying(160) NOT NULL,
    from_column character varying(160),
    to_table character varying(160) NOT NULL,
    to_column character varying(160),
    rel_type character varying(80),
    provenance character varying(40) DEFAULT 'schema_defined'::character varying NOT NULL,
    confidence numeric(5,2),
    review_status character varying(40),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: udr_script; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.udr_script (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    script_code character varying(200) NOT NULL,
    status character varying(40),
    structured_doc_count integer DEFAULT 0,
    unstructured_doc_count integer DEFAULT 0,
    table_count integer DEFAULT 0,
    column_count integer DEFAULT 0,
    recommended_structure_md text,
    snapshot jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);


--
-- Name: udr_script_version; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.udr_script_version (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    udr_script_id uuid NOT NULL,
    org_id uuid NOT NULL,
    version_no integer NOT NULL,
    script_code character varying(200) NOT NULL,
    is_archived boolean DEFAULT false,
    change_summary text,
    snapshot jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: udr_synonym; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.udr_synonym (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    cmms_system character varying(80),
    source_term character varying(200) NOT NULL,
    target_kind character varying(20) NOT NULL,
    canonical_table character varying(160),
    canonical_column character varying(160),
    confidence numeric(4,3),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: udr_test_result; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.udr_test_result (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    udr_script_id uuid NOT NULL,
    test_name character varying(20) NOT NULL,
    total_checked integer,
    passed integer,
    failed integer,
    fail_rate numeric(6,4),
    blocked boolean DEFAULT false,
    detail jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_certifications; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.user_certifications (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    issued_by character varying(255),
    issued_date date,
    expiry_date date,
    document_url text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: user_roles; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.user_roles (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    role_id uuid NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid NOT NULL,
    full_name character varying(255) NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(500) NOT NULL,
    phone character varying(50),
    phone2 character varying(50),
    personnel_code character varying(100),
    hourly_rate numeric(10,2),
    is_group boolean DEFAULT false NOT NULL,
    status character varying(50) DEFAULT 'active'::character varying NOT NULL,
    role character varying(20) DEFAULT 'user'::character varying NOT NULL,
    last_login_at timestamp without time zone,
    email_verified boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    password_changed_at timestamp with time zone,
    failed_login_count integer DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    email_verified_at timestamp with time zone,
    CONSTRAINT ck_users_role CHECK (((role)::text = ANY ((ARRAY['superadmin'::character varying, 'admin'::character varying, 'user'::character varying])::text[])))
);


--
-- Name: vendor_contacts; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.vendor_contacts (
    id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    email character varying(255),
    phone character varying(50),
    designation character varying(150)
);


--
-- Name: vendor_contracts; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.vendor_contracts (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    vendor_id uuid NOT NULL,
    contract_name character varying(255) NOT NULL,
    contract_start date,
    contract_end date,
    contract_value numeric(18,2),
    sla_terms text,
    contract_document text,
    status character varying(50),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: vendor_monthly_scorecards; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.vendor_monthly_scorecards (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    vendor_id uuid NOT NULL,
    score_month date NOT NULL,
    overall_score numeric(6,2),
    trend_delta numeric(6,2),
    component_breakdown jsonb DEFAULT '{}'::jsonb NOT NULL,
    ppm_compliance_pct numeric(6,2),
    matched_flagged_ratio numeric(6,4),
    wo_score_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    block_capped boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: vendor_score_weight_config; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.vendor_score_weight_config (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    sla_response_pct numeric(5,2) DEFAULT 25 NOT NULL,
    sla_completion_pct numeric(5,2) DEFAULT 25 NOT NULL,
    first_fix_pct numeric(5,2) DEFAULT 20 NOT NULL,
    recall_pct numeric(5,2) DEFAULT 15 NOT NULL,
    accreditation_pct numeric(5,2) DEFAULT 15 NOT NULL,
    blocked_score_cap numeric(5,2) DEFAULT 60 NOT NULL,
    cost_variance_alert_pct numeric(5,2) DEFAULT 15 NOT NULL,
    cost_variance_job_count integer DEFAULT 3 NOT NULL,
    invoice_flag_adversary_gbp numeric(12,2) DEFAULT 500 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: vendor_wo_scores; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.vendor_wo_scores (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    organization_id uuid,
    vendor_id uuid,
    work_order_id uuid,
    wo_code character varying(120),
    asset_id uuid,
    score_month date,
    sla_response_met boolean,
    sla_completion_met boolean,
    first_fix boolean,
    recall boolean,
    accreditation_current boolean,
    component_scores jsonb DEFAULT '{}'::jsonb NOT NULL,
    overall_score numeric(6,2),
    capped_by_block boolean DEFAULT false NOT NULL,
    cost_actual numeric(14,2),
    cost_estimated numeric(14,2),
    cost_variance_pct numeric(8,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: vendors; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.vendors (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    vendor_name character varying(255) NOT NULL,
    vendor_code character varying(100),
    address text,
    city character varying(150),
    province character varying(150),
    postal_code character varying(20),
    country character varying(100),
    phone character varying(50),
    fax character varying(50),
    website character varying(500),
    notes text,
    status character varying(50) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    block_state character varying(40) DEFAULT 'Clear'::character varying,
    block_reason text,
    block_date timestamp with time zone,
    blocked_accreditation_type character varying(160)
);


--
-- Name: work_order_assets; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_order_assets (
    id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    asset_id uuid NOT NULL
);


--
-- Name: work_order_attachments; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_order_attachments (
    id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    file_url text NOT NULL,
    uploaded_by uuid,
    uploaded_at timestamp without time zone DEFAULT now() NOT NULL,
    work_order_task_id uuid
);


--
-- Name: work_order_comments; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_order_comments (
    id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    user_id uuid,
    comment text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: work_order_history; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_order_history (
    id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    old_status character varying(50),
    new_status character varying(50),
    changed_by uuid,
    changed_at timestamp without time zone DEFAULT now() NOT NULL,
    notes text
);


--
-- Name: work_order_parts; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_order_parts (
    id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    part_id uuid NOT NULL,
    asset_id uuid,
    quantity_used integer NOT NULL,
    unit_cost numeric(18,2),
    CONSTRAINT chk_work_order_parts_quantity_used CHECK ((quantity_used > 0))
);


--
-- Name: work_order_resources; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_order_resources (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    work_order_id uuid NOT NULL,
    resource_id uuid NOT NULL,
    hours numeric(10,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: work_order_statuses; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_order_statuses (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    is_closed_state boolean DEFAULT false NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: work_order_tasks; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_order_tasks (
    id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    task_group_id uuid,
    title character varying(255) NOT NULL,
    description text,
    task_type integer,
    sort_order integer DEFAULT 0 NOT NULL,
    assigned_to uuid,
    completed_by uuid,
    estimated_hours numeric(10,2),
    actual_hours numeric(10,2),
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    completed_at timestamp without time zone
);


--
-- Name: work_order_users; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_order_users (
    id uuid NOT NULL,
    work_order_id uuid NOT NULL,
    user_id uuid NOT NULL,
    hours_spent numeric(10,2)
);


--
-- Name: work_orders; Type: TABLE; Schema: plenum_cafm; Owner: -
--

CREATE TABLE plenum_cafm.work_orders (
    id uuid NOT NULL,
    organization_id uuid NOT NULL,
    asset_id uuid,
    location_id uuid,
    wo_code character varying(150),
    title character varying(255) NOT NULL,
    description text,
    problem text,
    solution text,
    completion_notes text,
    priority character varying(50) DEFAULT 'medium'::character varying NOT NULL,
    priority_id uuid,
    status character varying(50) DEFAULT 'open'::character varying NOT NULL,
    status_id uuid,
    maintenance_type character varying(100),
    maintenance_type_id uuid,
    created_by uuid,
    requested_by_id uuid,
    completed_by_id uuid,
    assigned_technician uuid,
    assigned_vendor uuid,
    estimated_hours numeric(10,2),
    actual_hours numeric(10,2),
    sla_id uuid,
    sla_due_at timestamp without time zone,
    charge_department_id uuid,
    project_id uuid,
    task_group_id uuid,
    maintenance_plan_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    completed_at timestamp without time zone,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    conflict_flag boolean NOT NULL,
    conflict_payload jsonb,
    workorder_ref character varying(80),
    reported_at timestamp with time zone,
    attended_at timestamp with time zone,
    responded_at timestamp with time zone,
    first_fix boolean,
    recall boolean,
    return_visit boolean,
    estimated_cost numeric(14,2),
    actual_cost numeric(14,2),
    labour_hours numeric(10,2),
    part_code character varying(80),
    parts_cost numeric(14,2),
    wo_type character varying(80),
    vendor_id uuid,
    notes text,
    building_id uuid,
    contract_id uuid,
    raised_at timestamp with time zone,
    closed_at timestamp with time zone,
    site_id uuid
);


--
-- Name: activity_log_action activity_log_action_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.activity_log_action
    ADD CONSTRAINT activity_log_action_pkey PRIMARY KEY (id);


--
-- Name: activity_log_entry activity_log_entry_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.activity_log_entry
    ADD CONSTRAINT activity_log_entry_pkey PRIMARY KEY (id);


--
-- Name: activity_log_refinement activity_log_refinement_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.activity_log_refinement
    ADD CONSTRAINT activity_log_refinement_pkey PRIMARY KEY (id);


--
-- Name: activity_log_step activity_log_step_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.activity_log_step
    ADD CONSTRAINT activity_log_step_pkey PRIMARY KEY (id);


--
-- Name: agent_audit_log agent_audit_log_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.agent_audit_log
    ADD CONSTRAINT agent_audit_log_pkey PRIMARY KEY (id);


--
-- Name: approval_action_tokens approval_action_tokens_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.approval_action_tokens
    ADD CONSTRAINT approval_action_tokens_pkey PRIMARY KEY (id);


--
-- Name: approval_action_tokens approval_action_tokens_token_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.approval_action_tokens
    ADD CONSTRAINT approval_action_tokens_token_key UNIQUE (token);


--
-- Name: approvals_queue_items approvals_queue_items_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.approvals_queue_items
    ADD CONSTRAINT approvals_queue_items_pkey PRIMARY KEY (id);


--
-- Name: asset_categories asset_categories_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_categories
    ADD CONSTRAINT asset_categories_pkey PRIMARY KEY (id);


--
-- Name: asset_condition_scores asset_condition_scores_asset_id_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_condition_scores
    ADD CONSTRAINT asset_condition_scores_asset_id_key UNIQUE (asset_id);


--
-- Name: asset_condition_scores asset_condition_scores_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_condition_scores
    ADD CONSTRAINT asset_condition_scores_pkey PRIMARY KEY (id);


--
-- Name: asset_criticality asset_criticality_asset_id_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_criticality
    ADD CONSTRAINT asset_criticality_asset_id_key UNIQUE (asset_id);


--
-- Name: asset_criticality asset_criticality_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_criticality
    ADD CONSTRAINT asset_criticality_pkey PRIMARY KEY (id);


--
-- Name: asset_documents asset_documents_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_documents
    ADD CONSTRAINT asset_documents_pkey PRIMARY KEY (id);


--
-- Name: asset_offline_log asset_offline_log_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_offline_log
    ADD CONSTRAINT asset_offline_log_pkey PRIMARY KEY (id);


--
-- Name: asset_readings asset_readings_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_readings
    ADD CONSTRAINT asset_readings_pkey PRIMARY KEY (id);


--
-- Name: asset_types asset_types_org_id_asset_type_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_types
    ADD CONSTRAINT asset_types_org_id_asset_type_key UNIQUE (org_id, asset_type);


--
-- Name: asset_types asset_types_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_types
    ADD CONSTRAINT asset_types_pkey PRIMARY KEY (id);


--
-- Name: asset_warranties asset_warranties_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_warranties
    ADD CONSTRAINT asset_warranties_pkey PRIMARY KEY (id);


--
-- Name: assets assets_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: auth_otp_codes auth_otp_codes_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.auth_otp_codes
    ADD CONSTRAINT auth_otp_codes_pkey PRIMARY KEY (id);


--
-- Name: auth_role_changes auth_role_changes_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.auth_role_changes
    ADD CONSTRAINT auth_role_changes_pkey PRIMARY KEY (id);


--
-- Name: auth_sessions auth_sessions_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.auth_sessions
    ADD CONSTRAINT auth_sessions_pkey PRIMARY KEY (id);


--
-- Name: auth_sessions auth_sessions_refresh_token_hash_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.auth_sessions
    ADD CONSTRAINT auth_sessions_refresh_token_hash_key UNIQUE (refresh_token_hash);


--
-- Name: bom_group_parts bom_group_parts_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.bom_group_parts
    ADD CONSTRAINT bom_group_parts_pkey PRIMARY KEY (id);


--
-- Name: bom_groups bom_groups_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.bom_groups
    ADD CONSTRAINT bom_groups_pkey PRIMARY KEY (id);


--
-- Name: building_country_packs building_country_packs_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.building_country_packs
    ADD CONSTRAINT building_country_packs_pkey PRIMARY KEY (id);


--
-- Name: building_country_packs building_country_packs_site_id_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.building_country_packs
    ADD CONSTRAINT building_country_packs_site_id_key UNIQUE (site_id);


--
-- Name: building_energy_profiles building_energy_profiles_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.building_energy_profiles
    ADD CONSTRAINT building_energy_profiles_pkey PRIMARY KEY (id);


--
-- Name: building_energy_profiles building_energy_profiles_site_id_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.building_energy_profiles
    ADD CONSTRAINT building_energy_profiles_site_id_key UNIQUE (site_id);


--
-- Name: buildings buildings_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.buildings
    ADD CONSTRAINT buildings_pkey PRIMARY KEY (building_id);


--
-- Name: charge_departments charge_departments_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.charge_departments
    ADD CONSTRAINT charge_departments_pkey PRIMARY KEY (id);


--
-- Name: claude_api_usage claude_api_usage_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.claude_api_usage
    ADD CONSTRAINT claude_api_usage_pkey PRIMARY KEY (id);


--
-- Name: claude_budget_config claude_budget_config_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.claude_budget_config
    ADD CONSTRAINT claude_budget_config_pkey PRIMARY KEY (id);


--
-- Name: compliance_certificates compliance_certificates_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.compliance_certificates
    ADD CONSTRAINT compliance_certificates_pkey PRIMARY KEY (id);


--
-- Name: compliance_risk_snapshots compliance_risk_snapshots_organization_id_snapshot_date_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.compliance_risk_snapshots
    ADD CONSTRAINT compliance_risk_snapshots_organization_id_snapshot_date_key UNIQUE (organization_id, snapshot_date);


--
-- Name: compliance_risk_snapshots compliance_risk_snapshots_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.compliance_risk_snapshots
    ADD CONSTRAINT compliance_risk_snapshots_pkey PRIMARY KEY (id);


--
-- Name: compliance_scan_runs compliance_scan_runs_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.compliance_scan_runs
    ADD CONSTRAINT compliance_scan_runs_pkey PRIMARY KEY (id);


--
-- Name: compliance_vector_membership_audit compliance_vector_membership_audit_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.compliance_vector_membership_audit
    ADD CONSTRAINT compliance_vector_membership_audit_pkey PRIMARY KEY (id);


--
-- Name: compliance_verification_register_rows compliance_verification_register_rows_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.compliance_verification_register_rows
    ADD CONSTRAINT compliance_verification_register_rows_pkey PRIMARY KEY (id);


--
-- Name: compliance_verification_sources compliance_verification_sources_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.compliance_verification_sources
    ADD CONSTRAINT compliance_verification_sources_pkey PRIMARY KEY (certificate_type_code, country_code);


--
-- Name: contract_documents contract_documents_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.contract_documents
    ADD CONSTRAINT contract_documents_pkey PRIMARY KEY (id);


--
-- Name: contract_sla_parameters contract_sla_parameters_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.contract_sla_parameters
    ADD CONSTRAINT contract_sla_parameters_pkey PRIMARY KEY (id);


--
-- Name: corrections_log corrections_log_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.corrections_log
    ADD CONSTRAINT corrections_log_pkey PRIMARY KEY (id);


--
-- Name: cost_variance_alerts cost_variance_alerts_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.cost_variance_alerts
    ADD CONSTRAINT cost_variance_alerts_pkey PRIMARY KEY (id);


--
-- Name: country_certificate_packs country_certificate_packs_country_code_certificate_type_cod_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.country_certificate_packs
    ADD CONSTRAINT country_certificate_packs_country_code_certificate_type_cod_key UNIQUE (country_code, certificate_type_code, pack_version);


--
-- Name: country_certificate_packs country_certificate_packs_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.country_certificate_packs
    ADD CONSTRAINT country_certificate_packs_pkey PRIMARY KEY (id);


--
-- Name: document_chunks document_chunks_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.document_chunks
    ADD CONSTRAINT document_chunks_pkey PRIMARY KEY (id);


--
-- Name: document_generation_log document_generation_log_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.document_generation_log
    ADD CONSTRAINT document_generation_log_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (document_id);


--
-- Name: duty_holders duty_holders_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.duty_holders
    ADD CONSTRAINT duty_holders_pkey PRIMARY KEY (id);


--
-- Name: energy_anomalies energy_anomalies_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.energy_anomalies
    ADD CONSTRAINT energy_anomalies_pkey PRIMARY KEY (id);


--
-- Name: energy_meters energy_meters_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.energy_meters
    ADD CONSTRAINT energy_meters_pkey PRIMARY KEY (id);


--
-- Name: energy_monthly_reports energy_monthly_reports_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.energy_monthly_reports
    ADD CONSTRAINT energy_monthly_reports_pkey PRIMARY KEY (id);


--
-- Name: energy_monthly_reports energy_monthly_reports_site_id_report_month_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.energy_monthly_reports
    ADD CONSTRAINT energy_monthly_reports_site_id_report_month_key UNIQUE (site_id, report_month);


--
-- Name: energy_recommendations energy_recommendations_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.energy_recommendations
    ADD CONSTRAINT energy_recommendations_pkey PRIMARY KEY (id);


--
-- Name: equipment equipment_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.equipment
    ADD CONSTRAINT equipment_pkey PRIMARY KEY (equipment_id);


--
-- Name: eui_snapshots eui_snapshots_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.eui_snapshots
    ADD CONSTRAINT eui_snapshots_pkey PRIMARY KEY (id);


--
-- Name: files files_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.files
    ADD CONSTRAINT files_pkey PRIMARY KEY (id);


--
-- Name: floors floors_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.floors
    ADD CONSTRAINT floors_pkey PRIMARY KEY (floor_id);


--
-- Name: fm_report_staleness fm_report_staleness_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.fm_report_staleness
    ADD CONSTRAINT fm_report_staleness_pkey PRIMARY KEY (id);


--
-- Name: ingestion_audit_log ingestion_audit_log_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ingestion_audit_log
    ADD CONSTRAINT ingestion_audit_log_pkey PRIMARY KEY (id);


--
-- Name: ingestion_documents ingestion_documents_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ingestion_documents
    ADD CONSTRAINT ingestion_documents_pkey PRIMARY KEY (id);


--
-- Name: inspections inspections_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.inspections
    ADD CONSTRAINT inspections_pkey PRIMARY KEY (id);


--
-- Name: inventory_transactions inventory_transactions_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.inventory_transactions
    ADD CONSTRAINT inventory_transactions_pkey PRIMARY KEY (id);


--
-- Name: invoice_lines invoice_lines_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.invoice_lines
    ADD CONSTRAINT invoice_lines_pkey PRIMARY KEY (id);


--
-- Name: invoice_verifications invoice_verifications_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.invoice_verifications
    ADD CONSTRAINT invoice_verifications_pkey PRIMARY KEY (id);


--
-- Name: leases leases_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.leases
    ADD CONSTRAINT leases_pkey PRIMARY KEY (id);


--
-- Name: locations locations_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.locations
    ADD CONSTRAINT locations_pkey PRIMARY KEY (id);


--
-- Name: maintenance_history maintenance_history_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_history
    ADD CONSTRAINT maintenance_history_pkey PRIMARY KEY (id);


--
-- Name: maintenance_plans maintenance_plans_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_plans
    ADD CONSTRAINT maintenance_plans_pkey PRIMARY KEY (id);


--
-- Name: maintenance_types maintenance_types_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_types
    ADD CONSTRAINT maintenance_types_pkey PRIMARY KEY (id);


--
-- Name: manufacturers manufacturers_org_id_manufacturer_name_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.manufacturers
    ADD CONSTRAINT manufacturers_org_id_manufacturer_name_key UNIQUE (org_id, manufacturer_name);


--
-- Name: manufacturers manufacturers_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.manufacturers
    ADD CONSTRAINT manufacturers_pkey PRIMARY KEY (id);


--
-- Name: meter_reading_gaps meter_reading_gaps_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.meter_reading_gaps
    ADD CONSTRAINT meter_reading_gaps_pkey PRIMARY KEY (id);


--
-- Name: meter_reading_units meter_reading_units_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.meter_reading_units
    ADD CONSTRAINT meter_reading_units_pkey PRIMARY KEY (id);


--
-- Name: meter_readings meter_readings_meter_id_reading_at_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.meter_readings
    ADD CONSTRAINT meter_readings_meter_id_reading_at_key UNIQUE (meter_id, reading_at);


--
-- Name: meter_readings meter_readings_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.meter_readings
    ADD CONSTRAINT meter_readings_pkey PRIMARY KEY (id);


--
-- Name: meters meters_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.meters
    ADD CONSTRAINT meters_pkey PRIMARY KEY (meter_id);


--
-- Name: misc_cost_types misc_cost_types_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.misc_cost_types
    ADD CONSTRAINT misc_cost_types_pkey PRIMARY KEY (id);


--
-- Name: misc_costs misc_costs_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.misc_costs
    ADD CONSTRAINT misc_costs_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: ops_audit_log ops_audit_log_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ops_audit_log
    ADD CONSTRAINT ops_audit_log_pkey PRIMARY KEY (id);


--
-- Name: ops_email_log ops_email_log_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ops_email_log
    ADD CONSTRAINT ops_email_log_pkey PRIMARY KEY (id);


--
-- Name: orchestration_audit_log orchestration_audit_log_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.orchestration_audit_log
    ADD CONSTRAINT orchestration_audit_log_pkey PRIMARY KEY (id);


--
-- Name: organizations organizations_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (id);


--
-- Name: permissions permissions_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.permissions
    ADD CONSTRAINT permissions_pkey PRIMARY KEY (id);


--
-- Name: pinned_run pinned_run_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.pinned_run
    ADD CONSTRAINT pinned_run_pkey PRIMARY KEY (id);


--
-- Name: portfolios portfolios_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.portfolios
    ADD CONSTRAINT portfolios_pkey PRIMARY KEY (portfolio_id);


--
-- Name: ppm_visits ppm_visits_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ppm_visits
    ADD CONSTRAINT ppm_visits_pkey PRIMARY KEY (id);


--
-- Name: priorities priorities_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.priorities
    ADD CONSTRAINT priorities_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: prompt_ab_tests prompt_ab_tests_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.prompt_ab_tests
    ADD CONSTRAINT prompt_ab_tests_pkey PRIMARY KEY (id);


--
-- Name: prompt_templates prompt_templates_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.prompt_templates
    ADD CONSTRAINT prompt_templates_pkey PRIMARY KEY (id);


--
-- Name: purchase_order_line_items purchase_order_line_items_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_order_line_items
    ADD CONSTRAINT purchase_order_line_items_pkey PRIMARY KEY (id);


--
-- Name: purchase_order_statuses purchase_order_statuses_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_order_statuses
    ADD CONSTRAINT purchase_order_statuses_pkey PRIMARY KEY (id);


--
-- Name: purchase_orders purchase_orders_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_orders
    ADD CONSTRAINT purchase_orders_pkey PRIMARY KEY (id);


--
-- Name: query_audit_log query_audit_log_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.query_audit_log
    ADD CONSTRAINT query_audit_log_pkey PRIMARY KEY (id);


--
-- Name: rca_actions rca_actions_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_actions
    ADD CONSTRAINT rca_actions_pkey PRIMARY KEY (id);


--
-- Name: rca_causes rca_causes_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_causes
    ADD CONSTRAINT rca_causes_pkey PRIMARY KEY (id);


--
-- Name: rca_grouping_actions rca_grouping_actions_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_grouping_actions
    ADD CONSTRAINT rca_grouping_actions_pkey PRIMARY KEY (id);


--
-- Name: rca_grouping_causes rca_grouping_causes_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_grouping_causes
    ADD CONSTRAINT rca_grouping_causes_pkey PRIMARY KEY (id);


--
-- Name: rca_groupings rca_groupings_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_groupings
    ADD CONSTRAINT rca_groupings_pkey PRIMARY KEY (id);


--
-- Name: rca_problems rca_problems_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_problems
    ADD CONSTRAINT rca_problems_pkey PRIMARY KEY (id);


--
-- Name: receipt_line_items receipt_line_items_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.receipt_line_items
    ADD CONSTRAINT receipt_line_items_pkey PRIMARY KEY (id);


--
-- Name: receipts receipts_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.receipts
    ADD CONSTRAINT receipts_pkey PRIMARY KEY (id);


--
-- Name: regulation_packs regulation_packs_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.regulation_packs
    ADD CONSTRAINT regulation_packs_pkey PRIMARY KEY (pack_id);


--
-- Name: resource_skills resource_skills_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.resource_skills
    ADD CONSTRAINT resource_skills_pkey PRIMARY KEY (id);


--
-- Name: resources resources_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.resources
    ADD CONSTRAINT resources_pkey PRIMARY KEY (id);


--
-- Name: review_queue review_queue_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.review_queue
    ADD CONSTRAINT review_queue_pkey PRIMARY KEY (id);


--
-- Name: role_permissions role_permissions_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.role_permissions
    ADD CONSTRAINT role_permissions_pkey PRIMARY KEY (id);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: saved_space_item saved_space_item_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.saved_space_item
    ADD CONSTRAINT saved_space_item_pkey PRIMARY KEY (id);


--
-- Name: saved_space saved_space_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.saved_space
    ADD CONSTRAINT saved_space_pkey PRIMARY KEY (id);


--
-- Name: schedule_triggers schedule_triggers_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.schedule_triggers
    ADD CONSTRAINT schedule_triggers_pkey PRIMARY KEY (id);


--
-- Name: scheduled_maintenance_assets scheduled_maintenance_assets_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_assets
    ADD CONSTRAINT scheduled_maintenance_assets_pkey PRIMARY KEY (id);


--
-- Name: scheduled_maintenance_parts scheduled_maintenance_parts_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_parts
    ADD CONSTRAINT scheduled_maintenance_parts_pkey PRIMARY KEY (id);


--
-- Name: scheduled_maintenance_users scheduled_maintenance_users_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_users
    ADD CONSTRAINT scheduled_maintenance_users_pkey PRIMARY KEY (id);


--
-- Name: scheduled_tasks scheduled_tasks_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_tasks
    ADD CONSTRAINT scheduled_tasks_pkey PRIMARY KEY (id);


--
-- Name: site_occupancy_logs site_occupancy_logs_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.site_occupancy_logs
    ADD CONSTRAINT site_occupancy_logs_pkey PRIMARY KEY (id);


--
-- Name: sites sites_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.sites
    ADD CONSTRAINT sites_pkey PRIMARY KEY (site_id);


--
-- Name: sla_policies sla_policies_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.sla_policies
    ADD CONSTRAINT sla_policies_pkey PRIMARY KEY (id);


--
-- Name: spaces spaces_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.spaces
    ADD CONSTRAINT spaces_pkey PRIMARY KEY (space_id);


--
-- Name: spare_parts spare_parts_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.spare_parts
    ADD CONSTRAINT spare_parts_pkey PRIMARY KEY (id);


--
-- Name: task_groups task_groups_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.task_groups
    ADD CONSTRAINT task_groups_pkey PRIMARY KEY (id);


--
-- Name: technician_skills technician_skills_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.technician_skills
    ADD CONSTRAINT technician_skills_pkey PRIMARY KEY (id);


--
-- Name: technicians technicians_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.technicians
    ADD CONSTRAINT technicians_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- Name: udr_canonical_column udr_canonical_column_canonical_table_canonical_column_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_canonical_column
    ADD CONSTRAINT udr_canonical_column_canonical_table_canonical_column_key UNIQUE (canonical_table, canonical_column);


--
-- Name: udr_canonical_column udr_canonical_column_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_canonical_column
    ADD CONSTRAINT udr_canonical_column_pkey PRIMARY KEY (id);


--
-- Name: udr_canonical_table udr_canonical_table_canonical_table_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_canonical_table
    ADD CONSTRAINT udr_canonical_table_canonical_table_key UNIQUE (canonical_table);


--
-- Name: udr_canonical_table udr_canonical_table_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_canonical_table
    ADD CONSTRAINT udr_canonical_table_pkey PRIMARY KEY (id);


--
-- Name: udr_mapping_decision udr_mapping_decision_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_mapping_decision
    ADD CONSTRAINT udr_mapping_decision_pkey PRIMARY KEY (id);


--
-- Name: udr_ontology_chunk udr_ontology_chunk_chunk_key_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_ontology_chunk
    ADD CONSTRAINT udr_ontology_chunk_chunk_key_key UNIQUE (chunk_key);


--
-- Name: udr_ontology_chunk udr_ontology_chunk_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_ontology_chunk
    ADD CONSTRAINT udr_ontology_chunk_pkey PRIMARY KEY (id);


--
-- Name: udr_relationship udr_relationship_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_relationship
    ADD CONSTRAINT udr_relationship_pkey PRIMARY KEY (id);


--
-- Name: udr_script udr_script_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_script
    ADD CONSTRAINT udr_script_pkey PRIMARY KEY (id);


--
-- Name: udr_script_version udr_script_version_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_script_version
    ADD CONSTRAINT udr_script_version_pkey PRIMARY KEY (id);


--
-- Name: udr_script_version udr_script_version_udr_script_id_version_no_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_script_version
    ADD CONSTRAINT udr_script_version_udr_script_id_version_no_key UNIQUE (udr_script_id, version_no);


--
-- Name: udr_synonym udr_synonym_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_synonym
    ADD CONSTRAINT udr_synonym_pkey PRIMARY KEY (id);


--
-- Name: udr_test_result udr_test_result_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_test_result
    ADD CONSTRAINT udr_test_result_pkey PRIMARY KEY (id);


--
-- Name: asset_categories uq_asset_categories_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_categories
    ADD CONSTRAINT uq_asset_categories_org_name UNIQUE (organization_id, name);


--
-- Name: assets uq_assets_org_asset_code; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT uq_assets_org_asset_code UNIQUE (organization_id, asset_code);


--
-- Name: assets uq_assets_org_serial_number; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT uq_assets_org_serial_number UNIQUE (organization_id, serial_number);


--
-- Name: bom_group_parts uq_bom_group_parts; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.bom_group_parts
    ADD CONSTRAINT uq_bom_group_parts UNIQUE (bom_group_id, part_id);


--
-- Name: bom_groups uq_bom_groups_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.bom_groups
    ADD CONSTRAINT uq_bom_groups_org_name UNIQUE (organization_id, name);


--
-- Name: charge_departments uq_charge_departments_org_code; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.charge_departments
    ADD CONSTRAINT uq_charge_departments_org_code UNIQUE (organization_id, code);


--
-- Name: maintenance_types uq_maintenance_types_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_types
    ADD CONSTRAINT uq_maintenance_types_org_name UNIQUE (organization_id, name);


--
-- Name: meter_reading_units uq_meter_reading_units_org_symbol; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.meter_reading_units
    ADD CONSTRAINT uq_meter_reading_units_org_symbol UNIQUE (organization_id, symbol);


--
-- Name: misc_cost_types uq_misc_cost_types_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.misc_cost_types
    ADD CONSTRAINT uq_misc_cost_types_org_name UNIQUE (organization_id, name);


--
-- Name: permissions uq_permissions_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.permissions
    ADD CONSTRAINT uq_permissions_name UNIQUE (name);


--
-- Name: purchase_order_statuses uq_po_statuses_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_order_statuses
    ADD CONSTRAINT uq_po_statuses_org_name UNIQUE (organization_id, name);


--
-- Name: priorities uq_priorities_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.priorities
    ADD CONSTRAINT uq_priorities_org_name UNIQUE (organization_id, name);


--
-- Name: projects uq_projects_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.projects
    ADD CONSTRAINT uq_projects_org_name UNIQUE (organization_id, name);


--
-- Name: prompt_templates uq_prompt_template_version; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.prompt_templates
    ADD CONSTRAINT uq_prompt_template_version UNIQUE (agent_id, doc_type, version);


--
-- Name: rca_grouping_actions uq_rca_grouping_action; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_grouping_actions
    ADD CONSTRAINT uq_rca_grouping_action UNIQUE (rca_grouping_id, rca_action_id);


--
-- Name: rca_grouping_causes uq_rca_grouping_cause; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_grouping_causes
    ADD CONSTRAINT uq_rca_grouping_cause UNIQUE (rca_grouping_id, rca_cause_id);


--
-- Name: role_permissions uq_role_permissions; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.role_permissions
    ADD CONSTRAINT uq_role_permissions UNIQUE (role_id, permission_id);


--
-- Name: roles uq_roles_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.roles
    ADD CONSTRAINT uq_roles_org_name UNIQUE (organization_id, name);


--
-- Name: scheduled_maintenance_assets uq_sm_asset; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_assets
    ADD CONSTRAINT uq_sm_asset UNIQUE (maintenance_plan_id, asset_id);


--
-- Name: scheduled_maintenance_parts uq_sm_part; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_parts
    ADD CONSTRAINT uq_sm_part UNIQUE (maintenance_plan_id, part_id);


--
-- Name: scheduled_maintenance_users uq_sm_user; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_users
    ADD CONSTRAINT uq_sm_user UNIQUE (maintenance_plan_id, user_id);


--
-- Name: spare_parts uq_spare_parts_org_part_code; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.spare_parts
    ADD CONSTRAINT uq_spare_parts_org_part_code UNIQUE (organization_id, part_code);


--
-- Name: task_groups uq_task_groups_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.task_groups
    ADD CONSTRAINT uq_task_groups_org_name UNIQUE (organization_id, name);


--
-- Name: technicians uq_technicians_user; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.technicians
    ADD CONSTRAINT uq_technicians_user UNIQUE (user_id);


--
-- Name: user_roles uq_user_roles; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.user_roles
    ADD CONSTRAINT uq_user_roles UNIQUE (user_id, role_id);


--
-- Name: users uq_users_email; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.users
    ADD CONSTRAINT uq_users_email UNIQUE (email);


--
-- Name: vendors uq_vendors_org_code; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendors
    ADD CONSTRAINT uq_vendors_org_code UNIQUE (organization_id, vendor_code);


--
-- Name: work_order_assets uq_wo_asset; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_assets
    ADD CONSTRAINT uq_wo_asset UNIQUE (work_order_id, asset_id);


--
-- Name: work_order_statuses uq_wo_statuses_org_name; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_statuses
    ADD CONSTRAINT uq_wo_statuses_org_name UNIQUE (organization_id, name);


--
-- Name: work_order_users uq_wo_user; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_users
    ADD CONSTRAINT uq_wo_user UNIQUE (work_order_id, user_id);


--
-- Name: user_certifications user_certifications_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.user_certifications
    ADD CONSTRAINT user_certifications_pkey PRIMARY KEY (id);


--
-- Name: user_roles user_roles_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.user_roles
    ADD CONSTRAINT user_roles_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: vendor_contacts vendor_contacts_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_contacts
    ADD CONSTRAINT vendor_contacts_pkey PRIMARY KEY (id);


--
-- Name: vendor_contracts vendor_contracts_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_contracts
    ADD CONSTRAINT vendor_contracts_pkey PRIMARY KEY (id);


--
-- Name: vendor_monthly_scorecards vendor_monthly_scorecards_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_monthly_scorecards
    ADD CONSTRAINT vendor_monthly_scorecards_pkey PRIMARY KEY (id);


--
-- Name: vendor_monthly_scorecards vendor_monthly_scorecards_vendor_id_score_month_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_monthly_scorecards
    ADD CONSTRAINT vendor_monthly_scorecards_vendor_id_score_month_key UNIQUE (vendor_id, score_month);


--
-- Name: vendor_score_weight_config vendor_score_weight_config_organization_id_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_score_weight_config
    ADD CONSTRAINT vendor_score_weight_config_organization_id_key UNIQUE (organization_id);


--
-- Name: vendor_score_weight_config vendor_score_weight_config_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_score_weight_config
    ADD CONSTRAINT vendor_score_weight_config_pkey PRIMARY KEY (id);


--
-- Name: vendor_wo_scores vendor_wo_scores_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_wo_scores
    ADD CONSTRAINT vendor_wo_scores_pkey PRIMARY KEY (id);


--
-- Name: vendors vendors_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendors
    ADD CONSTRAINT vendors_pkey PRIMARY KEY (id);


--
-- Name: work_order_assets work_order_assets_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_assets
    ADD CONSTRAINT work_order_assets_pkey PRIMARY KEY (id);


--
-- Name: work_order_attachments work_order_attachments_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_attachments
    ADD CONSTRAINT work_order_attachments_pkey PRIMARY KEY (id);


--
-- Name: work_order_comments work_order_comments_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_comments
    ADD CONSTRAINT work_order_comments_pkey PRIMARY KEY (id);


--
-- Name: work_order_history work_order_history_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_history
    ADD CONSTRAINT work_order_history_pkey PRIMARY KEY (id);


--
-- Name: work_order_parts work_order_parts_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_parts
    ADD CONSTRAINT work_order_parts_pkey PRIMARY KEY (id);


--
-- Name: work_order_resources work_order_resources_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_resources
    ADD CONSTRAINT work_order_resources_pkey PRIMARY KEY (id);


--
-- Name: work_order_resources work_order_resources_work_order_id_resource_id_key; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_resources
    ADD CONSTRAINT work_order_resources_work_order_id_resource_id_key UNIQUE (work_order_id, resource_id);


--
-- Name: work_order_statuses work_order_statuses_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_statuses
    ADD CONSTRAINT work_order_statuses_pkey PRIMARY KEY (id);


--
-- Name: work_order_tasks work_order_tasks_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_tasks
    ADD CONSTRAINT work_order_tasks_pkey PRIMARY KEY (id);


--
-- Name: work_order_users work_order_users_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_users
    ADD CONSTRAINT work_order_users_pkey PRIMARY KEY (id);


--
-- Name: work_orders work_orders_pkey; Type: CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_pkey PRIMARY KEY (id);


--
-- Name: idx_cvs_country; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX idx_cvs_country ON plenum_cafm.compliance_verification_sources USING btree (country_code);


--
-- Name: ix_aat_token; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_aat_token ON plenum_cafm.approval_action_tokens USING btree (token);


--
-- Name: ix_ac_org; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ac_org ON plenum_cafm.asset_criticality USING btree (organization_id);


--
-- Name: ix_agent_audit_log_agent_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_agent_audit_log_agent_id ON plenum_cafm.agent_audit_log USING btree (agent_id);


--
-- Name: ix_agent_audit_log_asset_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_agent_audit_log_asset_code ON plenum_cafm.agent_audit_log USING btree (asset_code) WHERE (asset_code IS NOT NULL);


--
-- Name: ix_agent_audit_log_timestamp; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_agent_audit_log_timestamp ON plenum_cafm.agent_audit_log USING btree ("timestamp");


--
-- Name: ix_al_entry_org; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_al_entry_org ON plenum_cafm.activity_log_entry USING btree (org_id, created_at DESC);


--
-- Name: ix_al_step_entry; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_al_step_entry ON plenum_cafm.activity_log_step USING btree (entry_id);


--
-- Name: ix_aqi_org_status; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_aqi_org_status ON plenum_cafm.approvals_queue_items USING btree (organization_id, status, created_at DESC);


--
-- Name: ix_aqi_source; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_aqi_source ON plenum_cafm.approvals_queue_items USING btree (source_feature, severity);


--
-- Name: ix_asset_offline_log_asset_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_asset_offline_log_asset_id ON plenum_cafm.asset_offline_log USING btree (asset_id);


--
-- Name: ix_asset_offline_log_went_offline_at; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_asset_offline_log_went_offline_at ON plenum_cafm.asset_offline_log USING btree (went_offline_at);


--
-- Name: ix_asset_warranties_asset_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_asset_warranties_asset_id ON plenum_cafm.asset_warranties USING btree (asset_id);


--
-- Name: ix_assets_barcode; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_barcode ON plenum_cafm.assets USING btree (barcode) WHERE (barcode IS NOT NULL);


--
-- Name: ix_assets_building; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_building ON plenum_cafm.assets USING btree (building_id);


--
-- Name: ix_assets_charge_dept_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_charge_dept_id ON plenum_cafm.assets USING btree (charge_department_id) WHERE (charge_department_id IS NOT NULL);


--
-- Name: ix_assets_inventory_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_inventory_code ON plenum_cafm.assets USING btree (inventory_code) WHERE (inventory_code IS NOT NULL);


--
-- Name: ix_assets_manufacturer; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_manufacturer ON plenum_cafm.assets USING btree (manufacturer_id);


--
-- Name: ix_assets_parent_asset_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_parent_asset_id ON plenum_cafm.assets USING btree (parent_asset_id) WHERE (parent_asset_id IS NOT NULL);


--
-- Name: ix_assets_project_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_project_id ON plenum_cafm.assets USING btree (project_id) WHERE (project_id IS NOT NULL);


--
-- Name: ix_assets_raw_metadata; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_raw_metadata ON plenum_cafm.assets USING gin (raw_metadata) WHERE (raw_metadata IS NOT NULL);


--
-- Name: ix_assets_site; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_site ON plenum_cafm.assets USING btree (site_id);


--
-- Name: ix_assets_type; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_assets_type ON plenum_cafm.assets USING btree (asset_type_id);


--
-- Name: ix_auth_otp_email_purpose; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_auth_otp_email_purpose ON plenum_cafm.auth_otp_codes USING btree (email, purpose, created_at DESC);


--
-- Name: ix_auth_otp_expires; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_auth_otp_expires ON plenum_cafm.auth_otp_codes USING btree (expires_at);


--
-- Name: ix_auth_role_changes_user; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_auth_role_changes_user ON plenum_cafm.auth_role_changes USING btree (user_id, created_at DESC);


--
-- Name: ix_auth_sessions_user; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_auth_sessions_user ON plenum_cafm.auth_sessions USING btree (user_id, expires_at DESC);


--
-- Name: ix_bcp_org; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_bcp_org ON plenum_cafm.building_country_packs USING btree (organization_id);


--
-- Name: ix_bom_group_parts_bom_group_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_bom_group_parts_bom_group_id ON plenum_cafm.bom_group_parts USING btree (bom_group_id);


--
-- Name: ix_bom_group_parts_part_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_bom_group_parts_part_id ON plenum_cafm.bom_group_parts USING btree (part_id);


--
-- Name: ix_buildings_location; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_buildings_location ON plenum_cafm.buildings USING btree (location_id);


--
-- Name: ix_buildings_site; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_buildings_site ON plenum_cafm.buildings USING btree (site_id);


--
-- Name: ix_cc_asset; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cc_asset ON plenum_cafm.compliance_certificates USING btree (asset_id);


--
-- Name: ix_cc_expiry; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cc_expiry ON plenum_cafm.compliance_certificates USING btree (expiry_date);


--
-- Name: ix_cc_expiry2; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cc_expiry2 ON plenum_cafm.compliance_certificates USING btree (expiry_date);


--
-- Name: ix_cc_org; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cc_org ON plenum_cafm.compliance_certificates USING btree (organization_id);


--
-- Name: ix_cc_scope_status; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cc_scope_status ON plenum_cafm.compliance_certificates USING btree (cert_scope, status);


--
-- Name: ix_cc_type_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cc_type_code ON plenum_cafm.compliance_certificates USING btree (certificate_type_code);


--
-- Name: ix_cc_vendor; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cc_vendor ON plenum_cafm.compliance_certificates USING btree (vendor_id);


--
-- Name: ix_ccp_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ccp_code ON plenum_cafm.country_certificate_packs USING btree (certificate_type_code);


--
-- Name: ix_ccp_country_scope; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ccp_country_scope ON plenum_cafm.country_certificate_packs USING btree (country_code, certificate_scope);


--
-- Name: ix_claude_api_usage_ingestion_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_claude_api_usage_ingestion_id ON plenum_cafm.claude_api_usage USING btree (ingestion_id);


--
-- Name: ix_claude_api_usage_service_model; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_claude_api_usage_service_model ON plenum_cafm.claude_api_usage USING btree (service, model);


--
-- Name: ix_claude_api_usage_timestamp; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_claude_api_usage_timestamp ON plenum_cafm.claude_api_usage USING btree ("timestamp");


--
-- Name: ix_compliance_certificates_building; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_compliance_certificates_building ON plenum_cafm.compliance_certificates USING btree (building_id);


--
-- Name: ix_compliance_certificates_country_state; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_compliance_certificates_country_state ON plenum_cafm.compliance_certificates USING btree (country_code, state);


--
-- Name: ix_compliance_certificates_country_state_region; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_compliance_certificates_country_state_region ON plenum_cafm.compliance_certificates USING btree (country_code, state, region);


--
-- Name: ix_compliance_certificates_site_ref; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_compliance_certificates_site_ref ON plenum_cafm.compliance_certificates USING btree (cert_scope, site_ref);


--
-- Name: ix_corrections_log_ingestion_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_corrections_log_ingestion_id ON plenum_cafm.corrections_log USING btree (ingestion_id);


--
-- Name: ix_corrections_log_timestamp; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_corrections_log_timestamp ON plenum_cafm.corrections_log USING btree ("timestamp");


--
-- Name: ix_csp_org; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_csp_org ON plenum_cafm.contract_sla_parameters USING btree (organization_id);


--
-- Name: ix_csp_vendor; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_csp_vendor ON plenum_cafm.contract_sla_parameters USING btree (vendor_id);


--
-- Name: ix_cvma_created_at; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cvma_created_at ON plenum_cafm.compliance_vector_membership_audit USING btree (created_at DESC);


--
-- Name: ix_cvma_document_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cvma_document_id ON plenum_cafm.compliance_vector_membership_audit USING btree (document_id);


--
-- Name: ix_cvrr_code_key; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cvrr_code_key ON plenum_cafm.compliance_verification_register_rows USING btree (certificate_type_code, upper((lookup_key)::text));


--
-- Name: ix_cvrr_ingested_at; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_cvrr_ingested_at ON plenum_cafm.compliance_verification_register_rows USING btree (ingested_at DESC);


--
-- Name: ix_doc_chunks_anchor; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_doc_chunks_anchor ON plenum_cafm.document_chunks USING btree (source_entity_type, source_entity_id);


--
-- Name: ix_doc_chunks_embedding; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_doc_chunks_embedding ON plenum_cafm.document_chunks USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: ix_doc_generation_log_document_type; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_doc_generation_log_document_type ON plenum_cafm.document_generation_log USING btree (document_type);


--
-- Name: ix_doc_generation_log_held; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_doc_generation_log_held ON plenum_cafm.document_generation_log USING btree (held_for_review) WHERE (held_for_review = true);


--
-- Name: ix_doc_generation_log_timestamp; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_doc_generation_log_timestamp ON plenum_cafm.document_generation_log USING btree ("timestamp");


--
-- Name: ix_doc_generation_log_user_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_doc_generation_log_user_id ON plenum_cafm.document_generation_log USING btree (user_id) WHERE (user_id IS NOT NULL);


--
-- Name: ix_documents_building; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_documents_building ON plenum_cafm.documents USING btree (building_id);


--
-- Name: ix_ea_status; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ea_status ON plenum_cafm.energy_anomalies USING btree (status);


--
-- Name: ix_ea_type; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ea_type ON plenum_cafm.energy_anomalies USING btree (anomaly_type);


--
-- Name: ix_em_asset; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_em_asset ON plenum_cafm.energy_meters USING btree (asset_id);


--
-- Name: ix_em_mpan; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_em_mpan ON plenum_cafm.energy_meters USING btree (mpan);


--
-- Name: ix_em_mprn; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_em_mprn ON plenum_cafm.energy_meters USING btree (mprn);


--
-- Name: ix_em_site; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_em_site ON plenum_cafm.energy_meters USING btree (site_id);


--
-- Name: ix_energy_meters_simulate; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_energy_meters_simulate ON plenum_cafm.energy_meters USING btree (((raw_metadata ->> 'simulate'::text)));


--
-- Name: ix_equipment_asset; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_equipment_asset ON plenum_cafm.equipment USING btree (asset_id);


--
-- Name: ix_eui_site_period; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_eui_site_period ON plenum_cafm.eui_snapshots USING btree (site_id, period_start);


--
-- Name: ix_files_entity; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_files_entity ON plenum_cafm.files USING btree (entity_type, entity_id);


--
-- Name: ix_files_uploaded_at; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_files_uploaded_at ON plenum_cafm.files USING btree (uploaded_at);


--
-- Name: ix_floors_building; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_floors_building ON plenum_cafm.floors USING btree (building_id);


--
-- Name: ix_ingestion_audit_log_ingestion_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ingestion_audit_log_ingestion_id ON plenum_cafm.ingestion_audit_log USING btree (ingestion_id);


--
-- Name: ix_ingestion_audit_log_timestamp; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ingestion_audit_log_timestamp ON plenum_cafm.ingestion_audit_log USING btree ("timestamp");


--
-- Name: ix_ingestion_documents_file_hash; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ingestion_documents_file_hash ON plenum_cafm.ingestion_documents USING btree (file_hash_sha256);


--
-- Name: ix_ingestion_documents_tenant_status; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ingestion_documents_tenant_status ON plenum_cafm.ingestion_documents USING btree (tenant_id, status);


--
-- Name: ix_ingestion_documents_uploaded_at; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ingestion_documents_uploaded_at ON plenum_cafm.ingestion_documents USING btree (uploaded_at);


--
-- Name: ix_inspections_asset_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_inspections_asset_code ON plenum_cafm.inspections USING btree (asset_code) WHERE (asset_code IS NOT NULL);


--
-- Name: ix_inspections_asset_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_inspections_asset_id ON plenum_cafm.inspections USING btree (asset_id) WHERE (asset_id IS NOT NULL);


--
-- Name: ix_inspections_findings_jsonb; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_inspections_findings_jsonb ON plenum_cafm.inspections USING gin (findings_jsonb) WHERE (findings_jsonb IS NOT NULL);


--
-- Name: ix_inspections_inspection_date; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_inspections_inspection_date ON plenum_cafm.inspections USING btree (inspection_date);


--
-- Name: ix_inspections_risk_level; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_inspections_risk_level ON plenum_cafm.inspections USING btree (risk_level);


--
-- Name: ix_iv_vendor; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_iv_vendor ON plenum_cafm.invoice_verifications USING btree (vendor_id);


--
-- Name: ix_locations_building; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_locations_building ON plenum_cafm.locations USING btree (building_id);


--
-- Name: ix_locations_pack; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_locations_pack ON plenum_cafm.locations USING btree (pack_id);


--
-- Name: ix_locations_site; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_locations_site ON plenum_cafm.locations USING btree (site_id);


--
-- Name: ix_maintenance_plans_priority; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_maintenance_plans_priority ON plenum_cafm.maintenance_plans USING btree (priority_id) WHERE (priority_id IS NOT NULL);


--
-- Name: ix_maintenance_plans_sm_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_maintenance_plans_sm_code ON plenum_cafm.maintenance_plans USING btree (sm_code) WHERE (sm_code IS NOT NULL);


--
-- Name: ix_mapping_decision_script; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_mapping_decision_script ON plenum_cafm.udr_mapping_decision USING btree (udr_script_id);


--
-- Name: ix_meter_readings_meter; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_meter_readings_meter ON plenum_cafm.meter_readings USING btree (meter_id);


--
-- Name: ix_meters_asset; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_meters_asset ON plenum_cafm.meters USING btree (asset_id);


--
-- Name: ix_meters_building; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_meters_building ON plenum_cafm.meters USING btree (building_id);


--
-- Name: ix_misc_costs_work_order_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_misc_costs_work_order_id ON plenum_cafm.misc_costs USING btree (work_order_id);


--
-- Name: ix_mr_meter_at; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_mr_meter_at ON plenum_cafm.meter_readings USING btree (meter_id, reading_at);


--
-- Name: ix_mrg_status; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_mrg_status ON plenum_cafm.meter_reading_gaps USING btree (status);


--
-- Name: ix_ontology_chunk_embedding; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ontology_chunk_embedding ON plenum_cafm.udr_ontology_chunk USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: ix_ops_audit_org_ts; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_ops_audit_org_ts ON plenum_cafm.ops_audit_log USING btree (organization_id, created_at DESC);


--
-- Name: ix_orchestration_audit_log_action; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_orchestration_audit_log_action ON plenum_cafm.orchestration_audit_log USING btree (action);


--
-- Name: ix_orchestration_audit_log_asset_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_orchestration_audit_log_asset_code ON plenum_cafm.orchestration_audit_log USING btree (asset_code) WHERE (asset_code IS NOT NULL);


--
-- Name: ix_orchestration_audit_log_timestamp; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_orchestration_audit_log_timestamp ON plenum_cafm.orchestration_audit_log USING btree ("timestamp");


--
-- Name: ix_po_line_items_purchase_order_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_po_line_items_purchase_order_id ON plenum_cafm.purchase_order_line_items USING btree (purchase_order_id);


--
-- Name: ix_prompt_templates_agent_doc; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_prompt_templates_agent_doc ON plenum_cafm.prompt_templates USING btree (agent_id, doc_type);


--
-- Name: ix_purchase_orders_organization_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_purchase_orders_organization_id ON plenum_cafm.purchase_orders USING btree (organization_id);


--
-- Name: ix_purchase_orders_supplier_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_purchase_orders_supplier_id ON plenum_cafm.purchase_orders USING btree (supplier_id);


--
-- Name: ix_query_audit_log_retrieval_tier; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_query_audit_log_retrieval_tier ON plenum_cafm.query_audit_log USING btree (retrieval_tier);


--
-- Name: ix_query_audit_log_timestamp; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_query_audit_log_timestamp ON plenum_cafm.query_audit_log USING btree ("timestamp");


--
-- Name: ix_query_audit_log_user_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_query_audit_log_user_id ON plenum_cafm.query_audit_log USING btree (user_id);


--
-- Name: ix_rca_groupings_work_order_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_rca_groupings_work_order_id ON plenum_cafm.rca_groupings USING btree (work_order_id);


--
-- Name: ix_receipt_line_items_receipt_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_receipt_line_items_receipt_id ON plenum_cafm.receipt_line_items USING btree (receipt_id);


--
-- Name: ix_receipts_purchase_order_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_receipts_purchase_order_id ON plenum_cafm.receipts USING btree (purchase_order_id);


--
-- Name: ix_review_queue_ingestion_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_review_queue_ingestion_id ON plenum_cafm.review_queue USING btree (ingestion_id);


--
-- Name: ix_review_queue_status_created; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_review_queue_status_created ON plenum_cafm.review_queue USING btree (status, created_at);


--
-- Name: ix_rs_status; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_rs_status ON plenum_cafm.resource_skills USING btree (status);


--
-- Name: ix_rs_vendor; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_rs_vendor ON plenum_cafm.resource_skills USING btree (vendor_id);


--
-- Name: ix_saved_space_item_space; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_saved_space_item_space ON plenum_cafm.saved_space_item USING btree (space_id);


--
-- Name: ix_saved_space_org; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_saved_space_org ON plenum_cafm.saved_space USING btree (org_id);


--
-- Name: ix_schedule_triggers_plan_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_schedule_triggers_plan_id ON plenum_cafm.schedule_triggers USING btree (maintenance_plan_id);


--
-- Name: ix_scheduled_tasks_maintenance_plan_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_scheduled_tasks_maintenance_plan_id ON plenum_cafm.scheduled_tasks USING btree (maintenance_plan_id);


--
-- Name: ix_scheduled_tasks_task_group_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_scheduled_tasks_task_group_id ON plenum_cafm.scheduled_tasks USING btree (task_group_id);


--
-- Name: ix_sites_portfolio; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_sites_portfolio ON plenum_cafm.sites USING btree (portfolio_id);


--
-- Name: ix_sm_assets_asset_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_sm_assets_asset_id ON plenum_cafm.scheduled_maintenance_assets USING btree (asset_id);


--
-- Name: ix_sm_assets_plan_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_sm_assets_plan_id ON plenum_cafm.scheduled_maintenance_assets USING btree (maintenance_plan_id);


--
-- Name: ix_sm_parts_plan_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_sm_parts_plan_id ON plenum_cafm.scheduled_maintenance_parts USING btree (maintenance_plan_id);


--
-- Name: ix_sm_users_plan_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_sm_users_plan_id ON plenum_cafm.scheduled_maintenance_users USING btree (maintenance_plan_id);


--
-- Name: ix_sol_site_at; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_sol_site_at ON plenum_cafm.site_occupancy_logs USING btree (site_id, changed_at);


--
-- Name: ix_spaces_building; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_spaces_building ON plenum_cafm.spaces USING btree (building_id);


--
-- Name: ix_spaces_floor; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_spaces_floor ON plenum_cafm.spaces USING btree (floor_id);


--
-- Name: ix_spare_parts_bom_group; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_spare_parts_bom_group ON plenum_cafm.spare_parts USING btree (bom_group_id) WHERE (bom_group_id IS NOT NULL);


--
-- Name: ix_spare_parts_supplier; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_spare_parts_supplier ON plenum_cafm.spare_parts USING btree (supplier_id) WHERE (supplier_id IS NOT NULL);


--
-- Name: ix_udr_rel_from; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_udr_rel_from ON plenum_cafm.udr_relationship USING btree (from_table, from_column);


--
-- Name: ix_udr_rel_script; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_udr_rel_script ON plenum_cafm.udr_relationship USING btree (udr_script_id);


--
-- Name: ix_udr_script_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_udr_script_code ON plenum_cafm.udr_script USING btree (script_code);


--
-- Name: ix_udr_script_org; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_udr_script_org ON plenum_cafm.udr_script USING btree (org_id);


--
-- Name: ix_udr_synonym_term; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_udr_synonym_term ON plenum_cafm.udr_synonym USING btree (lower((source_term)::text));


--
-- Name: ix_user_certifications_user_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_user_certifications_user_id ON plenum_cafm.user_certifications USING btree (user_id);


--
-- Name: ix_users_role; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_users_role ON plenum_cafm.users USING btree (role);


--
-- Name: ix_vendors_block_state; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_vendors_block_state ON plenum_cafm.vendors USING btree (block_state);


--
-- Name: ix_vws_vendor_month; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_vws_vendor_month ON plenum_cafm.vendor_wo_scores USING btree (vendor_id, score_month);


--
-- Name: ix_wo_assets_asset_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_wo_assets_asset_id ON plenum_cafm.work_order_assets USING btree (asset_id);


--
-- Name: ix_wo_assets_work_order_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_wo_assets_work_order_id ON plenum_cafm.work_order_assets USING btree (work_order_id);


--
-- Name: ix_wo_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_wo_code ON plenum_cafm.work_orders USING btree (wo_code);


--
-- Name: ix_wo_site; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_wo_site ON plenum_cafm.work_orders USING btree (site_id);


--
-- Name: ix_wo_users_work_order_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_wo_users_work_order_id ON plenum_cafm.work_order_users USING btree (work_order_id);


--
-- Name: ix_wo_vendor_completed; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_wo_vendor_completed ON plenum_cafm.work_orders USING btree (vendor_id, completed_at) WHERE (completed_at IS NOT NULL);


--
-- Name: ix_work_orders_asset; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_work_orders_asset ON plenum_cafm.work_orders USING btree (asset_id);


--
-- Name: ix_work_orders_building; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_work_orders_building ON plenum_cafm.work_orders USING btree (building_id);


--
-- Name: ix_work_orders_contract; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_work_orders_contract ON plenum_cafm.work_orders USING btree (contract_id);


--
-- Name: ix_work_orders_maintenance_plan; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_work_orders_maintenance_plan ON plenum_cafm.work_orders USING btree (maintenance_plan_id) WHERE (maintenance_plan_id IS NOT NULL);


--
-- Name: ix_work_orders_priority_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_work_orders_priority_id ON plenum_cafm.work_orders USING btree (priority_id) WHERE (priority_id IS NOT NULL);


--
-- Name: ix_work_orders_status_id; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_work_orders_status_id ON plenum_cafm.work_orders USING btree (status_id) WHERE (status_id IS NOT NULL);


--
-- Name: ix_work_orders_wo_code; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE INDEX ix_work_orders_wo_code ON plenum_cafm.work_orders USING btree (wo_code) WHERE (wo_code IS NOT NULL);


--
-- Name: uq_users_email_lower; Type: INDEX; Schema: plenum_cafm; Owner: -
--

CREATE UNIQUE INDEX uq_users_email_lower ON plenum_cafm.users USING btree (lower((email)::text));


--
-- Name: orchestration_audit_log no_delete_orchestration_audit_log; Type: RULE; Schema: plenum_cafm; Owner: -
--

CREATE RULE no_delete_orchestration_audit_log AS
    ON DELETE TO plenum_cafm.orchestration_audit_log DO INSTEAD NOTHING;


--
-- Name: orchestration_audit_log no_update_orchestration_audit_log; Type: RULE; Schema: plenum_cafm; Owner: -
--

CREATE RULE no_update_orchestration_audit_log AS
    ON UPDATE TO plenum_cafm.orchestration_audit_log DO INSTEAD NOTHING;


--
-- Name: ops_audit_log trg_ops_audit_no_delete; Type: TRIGGER; Schema: plenum_cafm; Owner: -
--

CREATE TRIGGER trg_ops_audit_no_delete BEFORE DELETE ON plenum_cafm.ops_audit_log FOR EACH ROW EXECUTE FUNCTION plenum_cafm.forbid_ops_audit_mutation();


--
-- Name: ops_audit_log trg_ops_audit_no_update; Type: TRIGGER; Schema: plenum_cafm; Owner: -
--

CREATE TRIGGER trg_ops_audit_no_update BEFORE UPDATE ON plenum_cafm.ops_audit_log FOR EACH ROW EXECUTE FUNCTION plenum_cafm.forbid_ops_audit_mutation();


--
-- Name: asset_categories asset_categories_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_categories
    ADD CONSTRAINT asset_categories_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: asset_categories asset_categories_parent_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_categories
    ADD CONSTRAINT asset_categories_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES plenum_cafm.asset_categories(id) ON DELETE SET NULL;


--
-- Name: asset_documents asset_documents_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_documents
    ADD CONSTRAINT asset_documents_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE CASCADE;


--
-- Name: asset_documents asset_documents_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_documents
    ADD CONSTRAINT asset_documents_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: asset_offline_log asset_offline_log_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_offline_log
    ADD CONSTRAINT asset_offline_log_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE CASCADE;


--
-- Name: asset_offline_log asset_offline_log_recorded_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_offline_log
    ADD CONSTRAINT asset_offline_log_recorded_by_fkey FOREIGN KEY (recorded_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: asset_offline_log asset_offline_log_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_offline_log
    ADD CONSTRAINT asset_offline_log_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE SET NULL;


--
-- Name: asset_readings asset_readings_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_readings
    ADD CONSTRAINT asset_readings_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE CASCADE;


--
-- Name: asset_readings asset_readings_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_readings
    ADD CONSTRAINT asset_readings_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: asset_readings asset_readings_submitted_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_readings
    ADD CONSTRAINT asset_readings_submitted_by_fkey FOREIGN KEY (submitted_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: asset_readings asset_readings_unit_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_readings
    ADD CONSTRAINT asset_readings_unit_id_fkey FOREIGN KEY (unit_id) REFERENCES plenum_cafm.meter_reading_units(id) ON DELETE SET NULL;


--
-- Name: asset_warranties asset_warranties_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.asset_warranties
    ADD CONSTRAINT asset_warranties_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE CASCADE;


--
-- Name: assets assets_category_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT assets_category_id_fkey FOREIGN KEY (category_id) REFERENCES plenum_cafm.asset_categories(id) ON DELETE SET NULL;


--
-- Name: assets assets_charge_department_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT assets_charge_department_id_fkey FOREIGN KEY (charge_department_id) REFERENCES plenum_cafm.charge_departments(id) ON DELETE SET NULL;


--
-- Name: assets assets_location_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT assets_location_id_fkey FOREIGN KEY (location_id) REFERENCES plenum_cafm.locations(id) ON DELETE SET NULL;


--
-- Name: assets assets_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT assets_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: assets assets_parent_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT assets_parent_asset_id_fkey FOREIGN KEY (parent_asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE SET NULL;


--
-- Name: assets assets_project_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT assets_project_id_fkey FOREIGN KEY (project_id) REFERENCES plenum_cafm.projects(id) ON DELETE SET NULL;


--
-- Name: audit_logs audit_logs_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.audit_logs
    ADD CONSTRAINT audit_logs_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: audit_logs audit_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.audit_logs
    ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: auth_otp_codes auth_otp_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.auth_otp_codes
    ADD CONSTRAINT auth_otp_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE CASCADE;


--
-- Name: auth_role_changes auth_role_changes_changed_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.auth_role_changes
    ADD CONSTRAINT auth_role_changes_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: auth_role_changes auth_role_changes_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.auth_role_changes
    ADD CONSTRAINT auth_role_changes_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE CASCADE;


--
-- Name: auth_sessions auth_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.auth_sessions
    ADD CONSTRAINT auth_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE CASCADE;


--
-- Name: bom_group_parts bom_group_parts_bom_group_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.bom_group_parts
    ADD CONSTRAINT bom_group_parts_bom_group_id_fkey FOREIGN KEY (bom_group_id) REFERENCES plenum_cafm.bom_groups(id) ON DELETE CASCADE;


--
-- Name: bom_group_parts bom_group_parts_part_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.bom_group_parts
    ADD CONSTRAINT bom_group_parts_part_id_fkey FOREIGN KEY (part_id) REFERENCES plenum_cafm.spare_parts(id) ON DELETE CASCADE;


--
-- Name: bom_groups bom_groups_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.bom_groups
    ADD CONSTRAINT bom_groups_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: buildings buildings_location_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.buildings
    ADD CONSTRAINT buildings_location_id_fkey FOREIGN KEY (location_id) REFERENCES plenum_cafm.locations(id);


--
-- Name: charge_departments charge_departments_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.charge_departments
    ADD CONSTRAINT charge_departments_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: claude_api_usage claude_api_usage_ingestion_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.claude_api_usage
    ADD CONSTRAINT claude_api_usage_ingestion_id_fkey FOREIGN KEY (ingestion_id) REFERENCES plenum_cafm.ingestion_documents(id) ON DELETE SET NULL;


--
-- Name: claude_api_usage claude_api_usage_query_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.claude_api_usage
    ADD CONSTRAINT claude_api_usage_query_id_fkey FOREIGN KEY (query_id) REFERENCES plenum_cafm.query_audit_log(id) ON DELETE SET NULL;


--
-- Name: corrections_log corrections_log_ingestion_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.corrections_log
    ADD CONSTRAINT corrections_log_ingestion_id_fkey FOREIGN KEY (ingestion_id) REFERENCES plenum_cafm.ingestion_documents(id) ON DELETE CASCADE;


--
-- Name: corrections_log corrections_log_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.corrections_log
    ADD CONSTRAINT corrections_log_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES plenum_cafm.users(id) ON DELETE CASCADE;


--
-- Name: document_generation_log document_generation_log_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.document_generation_log
    ADD CONSTRAINT document_generation_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: documents documents_building_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.documents
    ADD CONSTRAINT documents_building_id_fkey FOREIGN KEY (building_id) REFERENCES plenum_cafm.buildings(building_id);


--
-- Name: files files_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.files
    ADD CONSTRAINT files_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: files files_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.files
    ADD CONSTRAINT files_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: activity_log_action fk_ala_entry; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.activity_log_action
    ADD CONSTRAINT fk_ala_entry FOREIGN KEY (entry_id) REFERENCES plenum_cafm.activity_log_entry(id) ON DELETE SET NULL;


--
-- Name: activity_log_refinement fk_alr_entry; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.activity_log_refinement
    ADD CONSTRAINT fk_alr_entry FOREIGN KEY (entry_id) REFERENCES plenum_cafm.activity_log_entry(id) ON DELETE SET NULL;


--
-- Name: activity_log_step fk_als_entry; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.activity_log_step
    ADD CONSTRAINT fk_als_entry FOREIGN KEY (entry_id) REFERENCES plenum_cafm.activity_log_entry(id) ON DELETE SET NULL;


--
-- Name: assets fk_assets_manufacturer; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT fk_assets_manufacturer FOREIGN KEY (manufacturer_id) REFERENCES plenum_cafm.manufacturers(id) ON DELETE SET NULL;


--
-- Name: assets fk_assets_type; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.assets
    ADD CONSTRAINT fk_assets_type FOREIGN KEY (asset_type_id) REFERENCES plenum_cafm.asset_types(id) ON DELETE SET NULL;


--
-- Name: compliance_certificates fk_cc_asset; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.compliance_certificates
    ADD CONSTRAINT fk_cc_asset FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE SET NULL;


--
-- Name: compliance_certificates fk_cc_dutyholder; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.compliance_certificates
    ADD CONSTRAINT fk_cc_dutyholder FOREIGN KEY (duty_holder_id) REFERENCES plenum_cafm.duty_holders(id) ON DELETE SET NULL;


--
-- Name: leases fk_leases_tenant; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.leases
    ADD CONSTRAINT fk_leases_tenant FOREIGN KEY (tenant_id) REFERENCES plenum_cafm.tenants(id) ON DELETE SET NULL;


--
-- Name: resources fk_resources_vendor; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.resources
    ADD CONSTRAINT fk_resources_vendor FOREIGN KEY (vendor_id) REFERENCES plenum_cafm.vendors(id) ON DELETE SET NULL;


--
-- Name: saved_space_item fk_ssi_space; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.saved_space_item
    ADD CONSTRAINT fk_ssi_space FOREIGN KEY (space_id) REFERENCES plenum_cafm.saved_space(id) ON DELETE SET NULL;


--
-- Name: udr_canonical_column fk_ucc_table; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_canonical_column
    ADD CONSTRAINT fk_ucc_table FOREIGN KEY (canonical_table) REFERENCES plenum_cafm.udr_canonical_table(canonical_table) ON DELETE SET NULL;


--
-- Name: udr_mapping_decision fk_umd_script; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_mapping_decision
    ADD CONSTRAINT fk_umd_script FOREIGN KEY (udr_script_id) REFERENCES plenum_cafm.udr_script(id) ON DELETE SET NULL;


--
-- Name: users fk_users_organization; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.users
    ADD CONSTRAINT fk_users_organization FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: udr_script_version fk_usv_script; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_script_version
    ADD CONSTRAINT fk_usv_script FOREIGN KEY (udr_script_id) REFERENCES plenum_cafm.udr_script(id) ON DELETE SET NULL;


--
-- Name: udr_test_result fk_utr_script; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.udr_test_result
    ADD CONSTRAINT fk_utr_script FOREIGN KEY (udr_script_id) REFERENCES plenum_cafm.udr_script(id) ON DELETE SET NULL;


--
-- Name: work_order_resources fk_wor_resource; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_resources
    ADD CONSTRAINT fk_wor_resource FOREIGN KEY (resource_id) REFERENCES plenum_cafm.resources(id) ON DELETE SET NULL;


--
-- Name: work_order_resources fk_wor_wo; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_resources
    ADD CONSTRAINT fk_wor_wo FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE SET NULL;


--
-- Name: floors floors_building_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.floors
    ADD CONSTRAINT floors_building_id_fkey FOREIGN KEY (building_id) REFERENCES plenum_cafm.buildings(building_id);


--
-- Name: ingestion_audit_log ingestion_audit_log_ingestion_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ingestion_audit_log
    ADD CONSTRAINT ingestion_audit_log_ingestion_id_fkey FOREIGN KEY (ingestion_id) REFERENCES plenum_cafm.ingestion_documents(id) ON DELETE CASCADE;


--
-- Name: ingestion_audit_log ingestion_audit_log_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ingestion_audit_log
    ADD CONSTRAINT ingestion_audit_log_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: ingestion_documents ingestion_documents_prompt_template_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ingestion_documents
    ADD CONSTRAINT ingestion_documents_prompt_template_id_fkey FOREIGN KEY (prompt_template_id) REFERENCES plenum_cafm.prompt_templates(id) ON DELETE SET NULL;


--
-- Name: ingestion_documents ingestion_documents_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ingestion_documents
    ADD CONSTRAINT ingestion_documents_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: inspections inspections_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.inspections
    ADD CONSTRAINT inspections_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE SET NULL;


--
-- Name: inspections inspections_ingestion_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.inspections
    ADD CONSTRAINT inspections_ingestion_id_fkey FOREIGN KEY (ingestion_id) REFERENCES plenum_cafm.ingestion_documents(id) ON DELETE SET NULL;


--
-- Name: inventory_transactions inventory_transactions_part_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.inventory_transactions
    ADD CONSTRAINT inventory_transactions_part_id_fkey FOREIGN KEY (part_id) REFERENCES plenum_cafm.spare_parts(id) ON DELETE CASCADE;


--
-- Name: locations locations_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.locations
    ADD CONSTRAINT locations_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: locations locations_pack_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.locations
    ADD CONSTRAINT locations_pack_id_fkey FOREIGN KEY (pack_id) REFERENCES plenum_cafm.regulation_packs(pack_id);


--
-- Name: locations locations_parent_location_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.locations
    ADD CONSTRAINT locations_parent_location_id_fkey FOREIGN KEY (parent_location_id) REFERENCES plenum_cafm.locations(id) ON DELETE SET NULL;


--
-- Name: maintenance_history maintenance_history_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_history
    ADD CONSTRAINT maintenance_history_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE CASCADE;


--
-- Name: maintenance_history maintenance_history_performed_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_history
    ADD CONSTRAINT maintenance_history_performed_by_fkey FOREIGN KEY (performed_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: maintenance_history maintenance_history_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_history
    ADD CONSTRAINT maintenance_history_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE SET NULL;


--
-- Name: maintenance_plans maintenance_plans_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_plans
    ADD CONSTRAINT maintenance_plans_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE CASCADE;


--
-- Name: maintenance_plans maintenance_plans_charge_department_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_plans
    ADD CONSTRAINT maintenance_plans_charge_department_id_fkey FOREIGN KEY (charge_department_id) REFERENCES plenum_cafm.charge_departments(id) ON DELETE SET NULL;


--
-- Name: maintenance_plans maintenance_plans_maintenance_type_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_plans
    ADD CONSTRAINT maintenance_plans_maintenance_type_id_fkey FOREIGN KEY (maintenance_type_id) REFERENCES plenum_cafm.maintenance_types(id) ON DELETE SET NULL;


--
-- Name: maintenance_plans maintenance_plans_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_plans
    ADD CONSTRAINT maintenance_plans_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: maintenance_plans maintenance_plans_priority_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_plans
    ADD CONSTRAINT maintenance_plans_priority_id_fkey FOREIGN KEY (priority_id) REFERENCES plenum_cafm.priorities(id) ON DELETE SET NULL;


--
-- Name: maintenance_plans maintenance_plans_project_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_plans
    ADD CONSTRAINT maintenance_plans_project_id_fkey FOREIGN KEY (project_id) REFERENCES plenum_cafm.projects(id) ON DELETE SET NULL;


--
-- Name: maintenance_plans maintenance_plans_task_group_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_plans
    ADD CONSTRAINT maintenance_plans_task_group_id_fkey FOREIGN KEY (task_group_id) REFERENCES plenum_cafm.task_groups(id) ON DELETE SET NULL;


--
-- Name: maintenance_types maintenance_types_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.maintenance_types
    ADD CONSTRAINT maintenance_types_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: meter_reading_units meter_reading_units_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.meter_reading_units
    ADD CONSTRAINT meter_reading_units_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: meter_readings meter_readings_meter_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.meter_readings
    ADD CONSTRAINT meter_readings_meter_id_fkey FOREIGN KEY (meter_id) REFERENCES plenum_cafm.energy_meters(id);


--
-- Name: meters meters_building_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.meters
    ADD CONSTRAINT meters_building_id_fkey FOREIGN KEY (building_id) REFERENCES plenum_cafm.buildings(building_id);


--
-- Name: misc_cost_types misc_cost_types_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.misc_cost_types
    ADD CONSTRAINT misc_cost_types_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: misc_costs misc_costs_created_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.misc_costs
    ADD CONSTRAINT misc_costs_created_by_fkey FOREIGN KEY (created_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: misc_costs misc_costs_misc_cost_type_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.misc_costs
    ADD CONSTRAINT misc_costs_misc_cost_type_id_fkey FOREIGN KEY (misc_cost_type_id) REFERENCES plenum_cafm.misc_cost_types(id) ON DELETE SET NULL;


--
-- Name: misc_costs misc_costs_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.misc_costs
    ADD CONSTRAINT misc_costs_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.notifications
    ADD CONSTRAINT notifications_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.notifications
    ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: ops_email_log ops_email_log_queue_item_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.ops_email_log
    ADD CONSTRAINT ops_email_log_queue_item_id_fkey FOREIGN KEY (queue_item_id) REFERENCES plenum_cafm.approvals_queue_items(id);


--
-- Name: priorities priorities_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.priorities
    ADD CONSTRAINT priorities_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: projects projects_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.projects
    ADD CONSTRAINT projects_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: projects projects_site_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.projects
    ADD CONSTRAINT projects_site_id_fkey FOREIGN KEY (site_id) REFERENCES plenum_cafm.locations(id) ON DELETE SET NULL;


--
-- Name: prompt_ab_tests prompt_ab_tests_template_a_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.prompt_ab_tests
    ADD CONSTRAINT prompt_ab_tests_template_a_id_fkey FOREIGN KEY (template_a_id) REFERENCES plenum_cafm.prompt_templates(id) ON DELETE CASCADE;


--
-- Name: prompt_ab_tests prompt_ab_tests_template_b_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.prompt_ab_tests
    ADD CONSTRAINT prompt_ab_tests_template_b_id_fkey FOREIGN KEY (template_b_id) REFERENCES plenum_cafm.prompt_templates(id) ON DELETE CASCADE;


--
-- Name: prompt_ab_tests prompt_ab_tests_winner_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.prompt_ab_tests
    ADD CONSTRAINT prompt_ab_tests_winner_id_fkey FOREIGN KEY (winner_id) REFERENCES plenum_cafm.prompt_templates(id) ON DELETE SET NULL;


--
-- Name: purchase_order_line_items purchase_order_line_items_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_order_line_items
    ADD CONSTRAINT purchase_order_line_items_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE SET NULL;


--
-- Name: purchase_order_line_items purchase_order_line_items_part_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_order_line_items
    ADD CONSTRAINT purchase_order_line_items_part_id_fkey FOREIGN KEY (part_id) REFERENCES plenum_cafm.spare_parts(id) ON DELETE SET NULL;


--
-- Name: purchase_order_line_items purchase_order_line_items_purchase_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_order_line_items
    ADD CONSTRAINT purchase_order_line_items_purchase_order_id_fkey FOREIGN KEY (purchase_order_id) REFERENCES plenum_cafm.purchase_orders(id) ON DELETE CASCADE;


--
-- Name: purchase_order_statuses purchase_order_statuses_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_order_statuses
    ADD CONSTRAINT purchase_order_statuses_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: purchase_orders purchase_orders_charge_department_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_orders
    ADD CONSTRAINT purchase_orders_charge_department_id_fkey FOREIGN KEY (charge_department_id) REFERENCES plenum_cafm.charge_departments(id) ON DELETE SET NULL;


--
-- Name: purchase_orders purchase_orders_created_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_orders
    ADD CONSTRAINT purchase_orders_created_by_fkey FOREIGN KEY (created_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: purchase_orders purchase_orders_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_orders
    ADD CONSTRAINT purchase_orders_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: purchase_orders purchase_orders_site_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_orders
    ADD CONSTRAINT purchase_orders_site_id_fkey FOREIGN KEY (site_id) REFERENCES plenum_cafm.locations(id) ON DELETE SET NULL;


--
-- Name: purchase_orders purchase_orders_status_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_orders
    ADD CONSTRAINT purchase_orders_status_id_fkey FOREIGN KEY (status_id) REFERENCES plenum_cafm.purchase_order_statuses(id) ON DELETE SET NULL;


--
-- Name: purchase_orders purchase_orders_supplier_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.purchase_orders
    ADD CONSTRAINT purchase_orders_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES plenum_cafm.vendors(id) ON DELETE SET NULL;


--
-- Name: query_audit_log query_audit_log_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.query_audit_log
    ADD CONSTRAINT query_audit_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: rca_actions rca_actions_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_actions
    ADD CONSTRAINT rca_actions_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: rca_causes rca_causes_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_causes
    ADD CONSTRAINT rca_causes_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: rca_grouping_actions rca_grouping_actions_assigned_to_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_grouping_actions
    ADD CONSTRAINT rca_grouping_actions_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: rca_grouping_actions rca_grouping_actions_rca_action_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_grouping_actions
    ADD CONSTRAINT rca_grouping_actions_rca_action_id_fkey FOREIGN KEY (rca_action_id) REFERENCES plenum_cafm.rca_actions(id) ON DELETE CASCADE;


--
-- Name: rca_grouping_actions rca_grouping_actions_rca_grouping_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_grouping_actions
    ADD CONSTRAINT rca_grouping_actions_rca_grouping_id_fkey FOREIGN KEY (rca_grouping_id) REFERENCES plenum_cafm.rca_groupings(id) ON DELETE CASCADE;


--
-- Name: rca_grouping_causes rca_grouping_causes_rca_cause_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_grouping_causes
    ADD CONSTRAINT rca_grouping_causes_rca_cause_id_fkey FOREIGN KEY (rca_cause_id) REFERENCES plenum_cafm.rca_causes(id) ON DELETE CASCADE;


--
-- Name: rca_grouping_causes rca_grouping_causes_rca_grouping_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_grouping_causes
    ADD CONSTRAINT rca_grouping_causes_rca_grouping_id_fkey FOREIGN KEY (rca_grouping_id) REFERENCES plenum_cafm.rca_groupings(id) ON DELETE CASCADE;


--
-- Name: rca_groupings rca_groupings_problem_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_groupings
    ADD CONSTRAINT rca_groupings_problem_id_fkey FOREIGN KEY (problem_id) REFERENCES plenum_cafm.rca_problems(id) ON DELETE SET NULL;


--
-- Name: rca_groupings rca_groupings_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_groupings
    ADD CONSTRAINT rca_groupings_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE CASCADE;


--
-- Name: rca_problems rca_problems_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.rca_problems
    ADD CONSTRAINT rca_problems_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: receipt_line_items receipt_line_items_part_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.receipt_line_items
    ADD CONSTRAINT receipt_line_items_part_id_fkey FOREIGN KEY (part_id) REFERENCES plenum_cafm.spare_parts(id) ON DELETE SET NULL;


--
-- Name: receipt_line_items receipt_line_items_po_line_item_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.receipt_line_items
    ADD CONSTRAINT receipt_line_items_po_line_item_id_fkey FOREIGN KEY (po_line_item_id) REFERENCES plenum_cafm.purchase_order_line_items(id) ON DELETE SET NULL;


--
-- Name: receipt_line_items receipt_line_items_receipt_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.receipt_line_items
    ADD CONSTRAINT receipt_line_items_receipt_id_fkey FOREIGN KEY (receipt_id) REFERENCES plenum_cafm.receipts(id) ON DELETE CASCADE;


--
-- Name: receipts receipts_purchase_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.receipts
    ADD CONSTRAINT receipts_purchase_order_id_fkey FOREIGN KEY (purchase_order_id) REFERENCES plenum_cafm.purchase_orders(id) ON DELETE CASCADE;


--
-- Name: receipts receipts_received_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.receipts
    ADD CONSTRAINT receipts_received_by_fkey FOREIGN KEY (received_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: receipts receipts_site_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.receipts
    ADD CONSTRAINT receipts_site_id_fkey FOREIGN KEY (site_id) REFERENCES plenum_cafm.locations(id) ON DELETE SET NULL;


--
-- Name: review_queue review_queue_ingestion_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.review_queue
    ADD CONSTRAINT review_queue_ingestion_id_fkey FOREIGN KEY (ingestion_id) REFERENCES plenum_cafm.ingestion_documents(id) ON DELETE CASCADE;


--
-- Name: review_queue review_queue_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.review_queue
    ADD CONSTRAINT review_queue_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: role_permissions role_permissions_permission_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.role_permissions
    ADD CONSTRAINT role_permissions_permission_id_fkey FOREIGN KEY (permission_id) REFERENCES plenum_cafm.permissions(id) ON DELETE CASCADE;


--
-- Name: role_permissions role_permissions_role_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.role_permissions
    ADD CONSTRAINT role_permissions_role_id_fkey FOREIGN KEY (role_id) REFERENCES plenum_cafm.roles(id) ON DELETE CASCADE;


--
-- Name: roles roles_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.roles
    ADD CONSTRAINT roles_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: schedule_triggers schedule_triggers_maintenance_plan_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.schedule_triggers
    ADD CONSTRAINT schedule_triggers_maintenance_plan_id_fkey FOREIGN KEY (maintenance_plan_id) REFERENCES plenum_cafm.maintenance_plans(id) ON DELETE CASCADE;


--
-- Name: schedule_triggers schedule_triggers_meter_unit_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.schedule_triggers
    ADD CONSTRAINT schedule_triggers_meter_unit_id_fkey FOREIGN KEY (meter_unit_id) REFERENCES plenum_cafm.meter_reading_units(id) ON DELETE SET NULL;


--
-- Name: scheduled_maintenance_assets scheduled_maintenance_assets_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_assets
    ADD CONSTRAINT scheduled_maintenance_assets_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE CASCADE;


--
-- Name: scheduled_maintenance_assets scheduled_maintenance_assets_maintenance_plan_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_assets
    ADD CONSTRAINT scheduled_maintenance_assets_maintenance_plan_id_fkey FOREIGN KEY (maintenance_plan_id) REFERENCES plenum_cafm.maintenance_plans(id) ON DELETE CASCADE;


--
-- Name: scheduled_maintenance_parts scheduled_maintenance_parts_maintenance_plan_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_parts
    ADD CONSTRAINT scheduled_maintenance_parts_maintenance_plan_id_fkey FOREIGN KEY (maintenance_plan_id) REFERENCES plenum_cafm.maintenance_plans(id) ON DELETE CASCADE;


--
-- Name: scheduled_maintenance_parts scheduled_maintenance_parts_part_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_parts
    ADD CONSTRAINT scheduled_maintenance_parts_part_id_fkey FOREIGN KEY (part_id) REFERENCES plenum_cafm.spare_parts(id) ON DELETE CASCADE;


--
-- Name: scheduled_maintenance_users scheduled_maintenance_users_maintenance_plan_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_users
    ADD CONSTRAINT scheduled_maintenance_users_maintenance_plan_id_fkey FOREIGN KEY (maintenance_plan_id) REFERENCES plenum_cafm.maintenance_plans(id) ON DELETE CASCADE;


--
-- Name: scheduled_maintenance_users scheduled_maintenance_users_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_maintenance_users
    ADD CONSTRAINT scheduled_maintenance_users_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE CASCADE;


--
-- Name: scheduled_tasks scheduled_tasks_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_tasks
    ADD CONSTRAINT scheduled_tasks_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE SET NULL;


--
-- Name: scheduled_tasks scheduled_tasks_maintenance_plan_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_tasks
    ADD CONSTRAINT scheduled_tasks_maintenance_plan_id_fkey FOREIGN KEY (maintenance_plan_id) REFERENCES plenum_cafm.maintenance_plans(id) ON DELETE CASCADE;


--
-- Name: scheduled_tasks scheduled_tasks_task_group_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.scheduled_tasks
    ADD CONSTRAINT scheduled_tasks_task_group_id_fkey FOREIGN KEY (task_group_id) REFERENCES plenum_cafm.task_groups(id) ON DELETE CASCADE;


--
-- Name: sla_policies sla_policies_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.sla_policies
    ADD CONSTRAINT sla_policies_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: spaces spaces_building_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.spaces
    ADD CONSTRAINT spaces_building_id_fkey FOREIGN KEY (building_id) REFERENCES plenum_cafm.buildings(building_id);


--
-- Name: spaces spaces_floor_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.spaces
    ADD CONSTRAINT spaces_floor_id_fkey FOREIGN KEY (floor_id) REFERENCES plenum_cafm.floors(floor_id);


--
-- Name: spare_parts spare_parts_bom_group_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.spare_parts
    ADD CONSTRAINT spare_parts_bom_group_id_fkey FOREIGN KEY (bom_group_id) REFERENCES plenum_cafm.bom_groups(id) ON DELETE SET NULL;


--
-- Name: spare_parts spare_parts_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.spare_parts
    ADD CONSTRAINT spare_parts_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: spare_parts spare_parts_supplier_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.spare_parts
    ADD CONSTRAINT spare_parts_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES plenum_cafm.vendors(id) ON DELETE SET NULL;


--
-- Name: task_groups task_groups_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.task_groups
    ADD CONSTRAINT task_groups_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: technician_skills technician_skills_technician_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.technician_skills
    ADD CONSTRAINT technician_skills_technician_id_fkey FOREIGN KEY (technician_id) REFERENCES plenum_cafm.technicians(id) ON DELETE CASCADE;


--
-- Name: technicians technicians_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.technicians
    ADD CONSTRAINT technicians_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: technicians technicians_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.technicians
    ADD CONSTRAINT technicians_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE CASCADE;


--
-- Name: user_certifications user_certifications_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.user_certifications
    ADD CONSTRAINT user_certifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE CASCADE;


--
-- Name: user_roles user_roles_role_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.user_roles
    ADD CONSTRAINT user_roles_role_id_fkey FOREIGN KEY (role_id) REFERENCES plenum_cafm.roles(id) ON DELETE CASCADE;


--
-- Name: user_roles user_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.user_roles
    ADD CONSTRAINT user_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE CASCADE;


--
-- Name: users users_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.users
    ADD CONSTRAINT users_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: vendor_contacts vendor_contacts_vendor_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_contacts
    ADD CONSTRAINT vendor_contacts_vendor_id_fkey FOREIGN KEY (vendor_id) REFERENCES plenum_cafm.vendors(id) ON DELETE CASCADE;


--
-- Name: vendor_contracts vendor_contracts_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_contracts
    ADD CONSTRAINT vendor_contracts_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: vendor_contracts vendor_contracts_vendor_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendor_contracts
    ADD CONSTRAINT vendor_contracts_vendor_id_fkey FOREIGN KEY (vendor_id) REFERENCES plenum_cafm.vendors(id) ON DELETE CASCADE;


--
-- Name: vendors vendors_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.vendors
    ADD CONSTRAINT vendors_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: work_order_assets work_order_assets_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_assets
    ADD CONSTRAINT work_order_assets_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE CASCADE;


--
-- Name: work_order_assets work_order_assets_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_assets
    ADD CONSTRAINT work_order_assets_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE CASCADE;


--
-- Name: work_order_attachments work_order_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_attachments
    ADD CONSTRAINT work_order_attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: work_order_attachments work_order_attachments_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_attachments
    ADD CONSTRAINT work_order_attachments_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE CASCADE;


--
-- Name: work_order_attachments work_order_attachments_work_order_task_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_attachments
    ADD CONSTRAINT work_order_attachments_work_order_task_id_fkey FOREIGN KEY (work_order_task_id) REFERENCES plenum_cafm.work_order_tasks(id) ON DELETE CASCADE;


--
-- Name: work_order_comments work_order_comments_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_comments
    ADD CONSTRAINT work_order_comments_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: work_order_comments work_order_comments_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_comments
    ADD CONSTRAINT work_order_comments_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE CASCADE;


--
-- Name: work_order_history work_order_history_changed_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_history
    ADD CONSTRAINT work_order_history_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: work_order_history work_order_history_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_history
    ADD CONSTRAINT work_order_history_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE CASCADE;


--
-- Name: work_order_parts work_order_parts_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_parts
    ADD CONSTRAINT work_order_parts_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE SET NULL;


--
-- Name: work_order_parts work_order_parts_part_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_parts
    ADD CONSTRAINT work_order_parts_part_id_fkey FOREIGN KEY (part_id) REFERENCES plenum_cafm.spare_parts(id) ON DELETE CASCADE;


--
-- Name: work_order_parts work_order_parts_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_parts
    ADD CONSTRAINT work_order_parts_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE CASCADE;


--
-- Name: work_order_statuses work_order_statuses_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_statuses
    ADD CONSTRAINT work_order_statuses_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: work_order_tasks work_order_tasks_assigned_to_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_tasks
    ADD CONSTRAINT work_order_tasks_assigned_to_fkey FOREIGN KEY (assigned_to) REFERENCES plenum_cafm.technicians(id) ON DELETE SET NULL;


--
-- Name: work_order_tasks work_order_tasks_completed_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_tasks
    ADD CONSTRAINT work_order_tasks_completed_by_fkey FOREIGN KEY (completed_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: work_order_tasks work_order_tasks_task_group_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_tasks
    ADD CONSTRAINT work_order_tasks_task_group_id_fkey FOREIGN KEY (task_group_id) REFERENCES plenum_cafm.task_groups(id) ON DELETE SET NULL;


--
-- Name: work_order_tasks work_order_tasks_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_tasks
    ADD CONSTRAINT work_order_tasks_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE CASCADE;


--
-- Name: work_order_users work_order_users_user_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_users
    ADD CONSTRAINT work_order_users_user_id_fkey FOREIGN KEY (user_id) REFERENCES plenum_cafm.users(id) ON DELETE CASCADE;


--
-- Name: work_order_users work_order_users_work_order_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_order_users
    ADD CONSTRAINT work_order_users_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES plenum_cafm.work_orders(id) ON DELETE CASCADE;


--
-- Name: work_orders work_orders_asset_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES plenum_cafm.assets(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_assigned_technician_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_assigned_technician_fkey FOREIGN KEY (assigned_technician) REFERENCES plenum_cafm.technicians(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_assigned_vendor_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_assigned_vendor_fkey FOREIGN KEY (assigned_vendor) REFERENCES plenum_cafm.vendors(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_charge_department_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_charge_department_id_fkey FOREIGN KEY (charge_department_id) REFERENCES plenum_cafm.charge_departments(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_completed_by_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_completed_by_id_fkey FOREIGN KEY (completed_by_id) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_created_by_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_created_by_fkey FOREIGN KEY (created_by) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_location_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_location_id_fkey FOREIGN KEY (location_id) REFERENCES plenum_cafm.locations(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_maintenance_plan_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_maintenance_plan_id_fkey FOREIGN KEY (maintenance_plan_id) REFERENCES plenum_cafm.maintenance_plans(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_maintenance_type_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_maintenance_type_id_fkey FOREIGN KEY (maintenance_type_id) REFERENCES plenum_cafm.maintenance_types(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_organization_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_organization_id_fkey FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations(id) ON DELETE CASCADE;


--
-- Name: work_orders work_orders_priority_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_priority_id_fkey FOREIGN KEY (priority_id) REFERENCES plenum_cafm.priorities(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_project_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_project_id_fkey FOREIGN KEY (project_id) REFERENCES plenum_cafm.projects(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_requested_by_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_requested_by_id_fkey FOREIGN KEY (requested_by_id) REFERENCES plenum_cafm.users(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_sla_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_sla_id_fkey FOREIGN KEY (sla_id) REFERENCES plenum_cafm.sla_policies(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_status_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_status_id_fkey FOREIGN KEY (status_id) REFERENCES plenum_cafm.work_order_statuses(id) ON DELETE SET NULL;


--
-- Name: work_orders work_orders_task_group_id_fkey; Type: FK CONSTRAINT; Schema: plenum_cafm; Owner: -
--

ALTER TABLE ONLY plenum_cafm.work_orders
    ADD CONSTRAINT work_orders_task_group_id_fkey FOREIGN KEY (task_group_id) REFERENCES plenum_cafm.task_groups(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict Jy6KNguMQmLjMUDkAYrtw4sEfyJdLBjngqIaEcH0YpbdEayH4UZllm615C8Cu1e


# Intelligence Engine Table Models

Schema: `plenum_cafm`  
Format: SQLAlchemy-style model stubs (table + columns only)

```python
class Asset(Base):
    __tablename__ = "assets"
    id
    organization_id
    location_id
    category_id
    asset_name
    asset_code
    serial_number
    manufacturer
    model_number
    installation_date
    warranty_expiry
    status
    health_score
    qr_code
    created_at
    updated_at
    criticality
    external_asset_id
    barcode
    inventory_code
    make
    model
    is_online
    is_site
    notes
    parent_asset_id
    aisle
    row
    bin_number
    stock_location
    charge_department_id
    project_id
    account_code
    timezone
    raw_metadata
    country_id
    account_id
    site_id
    category
    serial
    location
    location_code


class Location(Base):
    __tablename__ = "locations"
    id
    organization_id
    name
    type
    parent_location_id
    level
    created_at
    address
    city
    province
    postal_code
    country
    timezone


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id
    organization_id
    asset_id
    location_id
    title
    description
    priority
    status
    created_by
    assigned_technician
    assigned_vendor
    sla_id
    sla_due_at
    created_at
    completed_at
    external_wo_id
    work_order_type
    responded_at
    fault_code
    cause_code
    resolution_code
    labor_minutes
    travel_minutes
    sla_response_target_mins
    sla_response_actual_mins
    sla_breached
    cost_parts_aed
    cost_vendor_aed
    wo_code
    problem
    solution
    completion_notes
    priority_id
    status_id
    maintenance_type
    maintenance_type_id
    requested_by_id
    completed_by_id
    estimated_hours
    actual_hours
    charge_department_id
    project_id
    task_group_id
    maintenance_plan_id
    updated_at
    account_id
    site_id
    asset_code
    wo_type
    wo_priority
    assigned_tech_id
    vendor_id
    source
    asset
    location
    issue_description
    task_description
    request_type
    approval_type
    requester_name
    requester_email
    requester_phone
    vendor
    manpower
    scheduled_date
    scheduled_time
    estimated_duration
    inspection_required
    special_requirements
    cmms_work_order_id
    journey_log_id
    source_reference
    approved_at
    prepared_at
    sent_to_cmms_at
    work_order_id


class JourneyLog(Base):
    __tablename__ = "wo_journey_logs"
    jlog_id
    work_order_id
    status
    milestones
    expected_timeline
    events
    current_step
    deviations
    completed
    created_at
    updated_at
    actual_start
    actual_end
    asset_id
    source_system
    journey_status
    assigned_technician_id
    assigned_technician_name
    team_members
    estimated_cost
    actual_cost
    estimated_duration_hours
    actual_duration_hours
    resources_used
    completion_quality_score
    customer_satisfaction_score
    notes
    status_change_history
    milestone_history
    created_by
    updated_by


class StatusHistory(Base):
    __tablename__ = "wo_status_history"
    history_id
    work_order_id
    from_status
    to_status
    changed_by
    notes
    changed_at


class ApprovalRequest(Base):
    __tablename__ = "wo_approval_requests"
    request_id
    work_order_id
    approval_type
    approver
    status
    notes
    requested_at
    responded_at


class PPMSchedule(Base):
    __tablename__ = "ppm_schedules"
    schedule_id
    asset_id
    asset_name
    location
    task_description
    task_type
    frequency
    priority
    estimated_duration_minutes
    required_skills
    required_tools
    required_parts
    safety_requirements
    active
    last_executed
    created_at


class MaintenanceHistory(Base):
    __tablename__ = "maintenance_history"
    id
    asset_id
    work_order_id
    performed_by
    performed_at
    notes


class SparePart(Base):
    __tablename__ = "spare_parts"
    id
    organization_id
    part_name
    part_code
    description
    unit_price
    stock_quantity
    reorder_level
    max_quantity
    unit_of_measure
    aisle
    row
    bin_number
    supplier_id
    bom_group_id
    updated_at


class WorkOrderPart(Base):
    __tablename__ = "work_order_parts"
    id
    work_order_id
    part_id
    quantity_used
    asset_id
    unit_cost


class Vendor(Base):
    __tablename__ = "vendors"
    id
    organization_id
    vendor_name
    address
    created_at
    vendor_code
    city
    province
    postal_code
    country
    phone
    fax
    website
    notes
    status
    country_id
    business_group_id
    classification_id


class VendorContract(Base):
    __tablename__ = "vendor_contracts"
    id
    organization_id
    vendor_id
    contract_name
    contract_start
    contract_end
    contract_value
    sla_terms
    contract_document
    status
    created_at


class Technician(Base):
    __tablename__ = "technicians"
    id
    organization_id
    user_id
    base_location
    availability_status
    performance_score
    created_at


class TechnicianSkill(Base):
    __tablename__ = "technician_skills"
    id
    technician_id
    skill_name
    skill_level


class Inspection(Base):
    __tablename__ = "inspections"
    id
    asset_id
    asset_code
    ingestion_id
    inspector
    inspection_date
    section
    finding_type
    observations
    risk_level
    corrective_action
    source_file
    findings_jsonb
    created_at


class AssetReading(Base):
    __tablename__ = "asset_readings"
    id
    organization_id
    asset_id
    reading_type
    value
    unit
    recorded_at
    external_reading_id
    source
    anomaly_flag
    unit_id
    submitted_by


class AssetDocument(Base):
    __tablename__ = "asset_documents"
    id
    asset_id
    file_url
    document_type
    uploaded_by
    uploaded_at


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id
    ingestion_id
    source_filename
    doc_type
    chunk_index
    chunk_text
    embedding
    heading
    metadata
    created_at
```


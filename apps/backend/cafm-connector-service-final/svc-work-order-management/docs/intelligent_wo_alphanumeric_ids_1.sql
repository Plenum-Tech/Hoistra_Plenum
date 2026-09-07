-- ============================================================================
-- INTELLIGENT WORK ORDER ENGINE - ALPHANUMERIC IDs VERSION
-- ============================================================================
-- Database: plenum_cafm
-- Changes: Assets ID, site_id, and Vendor ID are now alphanumeric
-- Sample Data: 150+ interconnected records with alphanumeric IDs
-- ============================================================================

SET client_encoding = 'UTF8';
SET search_path = plenum_cafm;

-- ============================================================================
-- DROP EXISTING TABLES (clean slate — reverse dependency order)
-- ============================================================================

DROP TABLE IF EXISTS plenum_cafm.work_order_parts CASCADE;
DROP TABLE IF EXISTS plenum_cafm.wo_approval_requests CASCADE;
DROP TABLE IF EXISTS plenum_cafm.wo_status_history CASCADE;
DROP TABLE IF EXISTS plenum_cafm.wo_journey_logs CASCADE;
DROP TABLE IF EXISTS plenum_cafm.maintenance_history CASCADE;
DROP TABLE IF EXISTS plenum_cafm.inspections CASCADE;
DROP TABLE IF EXISTS plenum_cafm.asset_readings CASCADE;
DROP TABLE IF EXISTS plenum_cafm.certificates CASCADE;
DROP TABLE IF EXISTS plenum_cafm.known_issues CASCADE;
DROP TABLE IF EXISTS plenum_cafm.work_orders CASCADE;
DROP TABLE IF EXISTS plenum_cafm.ppm_schedules CASCADE;
DROP TABLE IF EXISTS plenum_cafm.spare_parts CASCADE;
DROP TABLE IF EXISTS plenum_cafm.technician_skills CASCADE;
DROP TABLE IF EXISTS plenum_cafm.technicians CASCADE;
DROP TABLE IF EXISTS plenum_cafm.vendor_contracts CASCADE;
DROP TABLE IF EXISTS plenum_cafm.vendors CASCADE;
DROP TABLE IF EXISTS plenum_cafm.assets CASCADE;
DROP TABLE IF EXISTS plenum_cafm.asset_categories CASCADE;
DROP TABLE IF EXISTS plenum_cafm.locations CASCADE;
DROP TABLE IF EXISTS plenum_cafm.sites CASCADE;
DROP TABLE IF EXISTS plenum_cafm.users CASCADE;
DROP TABLE IF EXISTS plenum_cafm.organizations CASCADE;
DROP TABLE IF EXISTS plenum_cafm.countries CASCADE;

-- ============================================================================
-- CORE TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS countries (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    code VARCHAR(3)
);

CREATE TABLE IF NOT EXISTS organizations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    code VARCHAR(50) UNIQUE NOT NULL,
    timezone VARCHAR(50) DEFAULT 'Asia/Dubai',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(200),
    role VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- SITES (Alphanumeric IDs)
-- ============================================================================

CREATE TABLE IF NOT EXISTS sites (
    site_id VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    site_name VARCHAR(200) NOT NULL,
    site_code VARCHAR(50) UNIQUE,
    city VARCHAR(100),
    country VARCHAR(100),
    timezone VARCHAR(50) DEFAULT 'Asia/Dubai',
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sites_org ON sites(organization_id);
CREATE INDEX IF NOT EXISTS idx_sites_code ON sites(site_code);

-- ============================================================================
-- LOCATIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS locations (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    site_id VARCHAR(50) REFERENCES sites(site_id),
    name VARCHAR(200) NOT NULL,
    type VARCHAR(50),
    parent_location_id INTEGER REFERENCES locations(id),
    level INTEGER,
    address TEXT,
    city VARCHAR(100),
    province VARCHAR(100),
    postal_code VARCHAR(20),
    country VARCHAR(100),
    timezone VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_locations_org ON locations(organization_id);
CREATE INDEX IF NOT EXISTS idx_locations_site ON locations(site_id);
CREATE INDEX IF NOT EXISTS idx_locations_parent ON locations(parent_location_id);

-- ============================================================================
-- ASSET CATEGORIES
-- ============================================================================

CREATE TABLE IF NOT EXISTS asset_categories (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    category_name VARCHAR(100) NOT NULL,
    parent_category_id INTEGER REFERENCES asset_categories(id)
);

-- ============================================================================
-- ASSETS (Alphanumeric ID)
-- ============================================================================

CREATE TABLE IF NOT EXISTS assets (
    id VARCHAR(50) PRIMARY KEY,  -- Changed to alphanumeric
    organization_id INTEGER REFERENCES organizations(id),
    site_id VARCHAR(50) REFERENCES sites(site_id),  -- Changed to alphanumeric
    location_id INTEGER REFERENCES locations(id),
    category_id INTEGER REFERENCES asset_categories(id),
    
    -- Identification
    asset_name VARCHAR(200) NOT NULL,
    asset_code VARCHAR(100) UNIQUE,
    serial_number VARCHAR(100),
    barcode VARCHAR(100),
    qr_code VARCHAR(100),
    
    -- Equipment Details
    manufacturer VARCHAR(100),
    model VARCHAR(100),
    model_number VARCHAR(100),
    make VARCHAR(100),
    
    -- Lifecycle
    installation_date DATE,
    warranty_expiry DATE,
    
    -- Status
    status VARCHAR(50) DEFAULT 'active',
    health_score INTEGER DEFAULT 100,
    criticality VARCHAR(20) DEFAULT 'medium',
    is_online BOOLEAN DEFAULT true,
    
    -- Location Details
    location VARCHAR(200),
    location_code VARCHAR(100),
    
    -- Hierarchy
    parent_asset_id VARCHAR(50) REFERENCES assets(id),  -- Changed to alphanumeric
    is_site BOOLEAN DEFAULT false,
    
    -- Metadata
    notes TEXT,
    raw_metadata JSONB,
    external_asset_id VARCHAR(100),
    inventory_code VARCHAR(100),
    
    -- Storage (for spare parts)
    aisle VARCHAR(20),
    row VARCHAR(20),
    bin_number VARCHAR(20),
    stock_location VARCHAR(200),
    
    -- Financial
    charge_department_id INTEGER,
    project_id INTEGER,
    account_code VARCHAR(50),
    account_id INTEGER,
    category VARCHAR(100),
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    timezone VARCHAR(50),
    country_id INTEGER REFERENCES countries(id)
);

CREATE INDEX IF NOT EXISTS idx_assets_org ON assets(organization_id);
CREATE INDEX IF NOT EXISTS idx_assets_site ON assets(site_id);
CREATE INDEX IF NOT EXISTS idx_assets_location ON assets(location_id);
CREATE INDEX IF NOT EXISTS idx_assets_code ON assets(asset_code);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);

-- ============================================================================
-- VENDORS (Alphanumeric ID)
-- ============================================================================

CREATE TABLE IF NOT EXISTS vendors (
    id VARCHAR(50) PRIMARY KEY,  -- Changed to alphanumeric
    organization_id INTEGER REFERENCES organizations(id),
    vendor_name VARCHAR(200) NOT NULL,
    vendor_code VARCHAR(50) UNIQUE,
    
    -- Contact
    address TEXT,
    city VARCHAR(100),
    province VARCHAR(100),
    postal_code VARCHAR(20),
    country VARCHAR(100),
    phone VARCHAR(50),
    fax VARCHAR(50),
    website VARCHAR(200),
    email VARCHAR(200),
    
    -- Status
    notes TEXT,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW(),
    country_id INTEGER REFERENCES countries(id),
    business_group_id INTEGER,
    classification_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_vendors_org ON vendors(organization_id);
CREATE INDEX IF NOT EXISTS idx_vendors_code ON vendors(vendor_code);

CREATE TABLE IF NOT EXISTS vendor_contracts (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    vendor_id VARCHAR(50) REFERENCES vendors(id),  -- Changed to alphanumeric
    contract_name VARCHAR(200),
    contract_start DATE,
    contract_end DATE,
    contract_value DECIMAL(12,2),
    sla_terms TEXT,
    contract_document VARCHAR(500),
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vendor_contracts_vendor ON vendor_contracts(vendor_id);

-- ============================================================================
-- TECHNICIANS
-- ============================================================================

CREATE TABLE IF NOT EXISTS technicians (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    user_id INTEGER REFERENCES users(id),
    base_location INTEGER REFERENCES locations(id),
    availability_status VARCHAR(50) DEFAULT 'available',
    performance_score INTEGER DEFAULT 80,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS technician_skills (
    id SERIAL PRIMARY KEY,
    technician_id INTEGER REFERENCES technicians(id),
    skill_name VARCHAR(100),
    skill_level VARCHAR(50) DEFAULT 'intermediate'
);

-- ============================================================================
-- SPARE PARTS
-- ============================================================================

CREATE TABLE IF NOT EXISTS spare_parts (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    part_name VARCHAR(200) NOT NULL,
    part_code VARCHAR(100) UNIQUE,
    description TEXT,
    unit_price DECIMAL(10,2),
    stock_quantity INTEGER DEFAULT 0,
    reorder_level INTEGER DEFAULT 5,
    max_quantity INTEGER,
    unit_of_measure VARCHAR(50),
    supplier_id VARCHAR(50) REFERENCES vendors(id),  -- Changed to alphanumeric
    bom_group_id INTEGER,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_parts_code ON spare_parts(part_code);
CREATE INDEX IF NOT EXISTS idx_parts_supplier ON spare_parts(supplier_id);

-- ============================================================================
-- PPM SCHEDULES
-- ============================================================================

CREATE TABLE IF NOT EXISTS ppm_schedules (
    schedule_id SERIAL PRIMARY KEY,
    asset_id VARCHAR(50) REFERENCES assets(id),  -- Changed to alphanumeric
    asset_name VARCHAR(200),
    location VARCHAR(200),
    task_description TEXT,
    task_type VARCHAR(100),
    frequency VARCHAR(50),
    priority VARCHAR(20) DEFAULT 'medium',
    estimated_duration_minutes INTEGER,
    required_skills TEXT[],
    required_parts JSONB,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ppm_asset ON ppm_schedules(asset_id);

-- ============================================================================
-- WORK ORDERS (Main Table with Intelligence)
-- ============================================================================

CREATE TABLE IF NOT EXISTS work_orders (
    id SERIAL PRIMARY KEY,
    work_order_id VARCHAR(50) UNIQUE NOT NULL,
    organization_id INTEGER REFERENCES organizations(id),
    
    -- Asset & Location (Updated references)
    asset_id VARCHAR(50) REFERENCES assets(id),  -- Changed to alphanumeric
    asset VARCHAR(100),
    asset_code VARCHAR(100),
    location_id INTEGER REFERENCES locations(id),
    location VARCHAR(200),
    site_id VARCHAR(50) REFERENCES sites(site_id),  -- Changed to alphanumeric
    
    -- Description
    title VARCHAR(500),
    description TEXT,
    issue_description TEXT,
    task_description TEXT,
    problem TEXT,
    solution TEXT,
    
    -- Classification
    priority VARCHAR(20),
    status VARCHAR(50) DEFAULT 'pending_approval',
    request_type VARCHAR(50) DEFAULT 'repair',
    work_order_type VARCHAR(100),
    maintenance_type VARCHAR(100),
    
    -- Source
    source VARCHAR(50),
    source_reference VARCHAR(200),
    external_wo_id VARCHAR(100),
    
    -- Assignment (Updated vendor reference)
    assigned_technician INTEGER REFERENCES technicians(id),
    assigned_vendor VARCHAR(50) REFERENCES vendors(id),  -- Changed to alphanumeric
    vendor VARCHAR(200),
    
    -- Scheduling
    scheduled_date VARCHAR(20),
    scheduled_time VARCHAR(20),
    estimated_duration DECIMAL(6,2),
    estimated_hours DECIMAL(6,2),
    actual_hours DECIMAL(6,2),
    
    -- Costs
    cost_parts_aed DECIMAL(10,2),
    cost_vendor_aed DECIMAL(10,2),
    
    -- Requester
    requester_name VARCHAR(200),
    requester_email VARCHAR(200),
    requester_phone VARCHAR(50),
    requested_by_id INTEGER REFERENCES users(id),

    -- Workflow
    approval_type VARCHAR(50),
    manpower JSONB,
    inspection_required BOOLEAN DEFAULT false,
    special_requirements TEXT,
    
    -- Intelligence JSONB fields (15-step engine)
    criticality JSONB,
    safety JSONB,
    compliance JSONB,
    asset_intelligence JSONB,
    spare_parts JSONB,
    parts_order_status JSONB,
    suggested_vendors JSONB,
    resource_allocation JSONB,
    schedule_constraints JSONB,
    
    -- Journey Reference
    journey_log_id VARCHAR(50),
    
    -- External Systems
    cmms_work_order_id VARCHAR(100),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    created_by INTEGER REFERENCES users(id),
    approved_at TIMESTAMP,
    prepared_at TIMESTAMP,
    completed_at TIMESTAMP,
    sent_to_cmms_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wo_org ON work_orders(organization_id);
CREATE INDEX IF NOT EXISTS idx_wo_asset ON work_orders(asset_id);
CREATE INDEX IF NOT EXISTS idx_wo_site ON work_orders(site_id);
CREATE INDEX IF NOT EXISTS idx_wo_status ON work_orders(status);
CREATE INDEX IF NOT EXISTS idx_wo_id ON work_orders(work_order_id);

-- ============================================================================
-- JOURNEY LOGS
-- ============================================================================

CREATE TABLE IF NOT EXISTS wo_journey_logs (
    jlog_id VARCHAR(50) PRIMARY KEY,
    work_order_id VARCHAR(50) REFERENCES work_orders(work_order_id),

    -- Status
    status VARCHAR(50) DEFAULT 'active',
    current_step VARCHAR(100),
    completed VARCHAR(10) DEFAULT 'false',
    journey_status VARCHAR(50),

    -- Timeline
    milestones JSONB,
    expected_timeline JSONB,
    actual_start TIMESTAMP,
    actual_end TIMESTAMP,

    -- Resources
    asset_id VARCHAR(50) REFERENCES assets(id),
    source_system VARCHAR(50),
    assigned_technician_id VARCHAR(100),
    assigned_technician_name VARCHAR(255),
    team_members JSONB,
    estimated_cost DECIMAL(15,2),
    actual_cost DECIMAL(15,2),
    estimated_duration_hours INTEGER,
    actual_duration_hours INTEGER,

    -- Tracking
    events JSONB,
    deviations JSONB,
    resources_used JSONB,
    status_change_history JSONB,
    milestone_history JSONB,
    completion_quality_score INTEGER,
    customer_satisfaction_score INTEGER,
    notes TEXT,

    created_by VARCHAR(100),
    updated_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_journey_wo ON wo_journey_logs(work_order_id);
CREATE INDEX IF NOT EXISTS idx_journey_asset ON wo_journey_logs(asset_id);

-- ============================================================================
-- SUPPORTING TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS wo_status_history (
    history_id VARCHAR(50) PRIMARY KEY,
    work_order_id VARCHAR(50) REFERENCES work_orders(work_order_id),
    from_status VARCHAR(50),
    to_status VARCHAR(50),
    changed_by VARCHAR(255) DEFAULT 'system',
    notes TEXT,
    changed_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_status_history_wo ON wo_status_history(work_order_id);

CREATE TABLE IF NOT EXISTS wo_approval_requests (
    request_id VARCHAR(50) PRIMARY KEY,
    work_order_id VARCHAR(50) REFERENCES work_orders(work_order_id),
    approval_type VARCHAR(50),
    approver VARCHAR(255),
    status VARCHAR(20) DEFAULT 'pending',
    notes TEXT,
    requested_at TIMESTAMP DEFAULT NOW(),
    responded_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_approval_wo ON wo_approval_requests(work_order_id);

CREATE TABLE IF NOT EXISTS work_order_parts (
    id SERIAL PRIMARY KEY,
    work_order_id VARCHAR(50) REFERENCES work_orders(work_order_id),
    part_id INTEGER REFERENCES spare_parts(id),
    quantity_used INTEGER,
    unit_cost DECIMAL(10,2),
    asset_id VARCHAR(50) REFERENCES assets(id)  -- Changed to alphanumeric
);

CREATE INDEX IF NOT EXISTS idx_wo_parts_wo ON work_order_parts(work_order_id);

CREATE TABLE IF NOT EXISTS maintenance_history (
    id SERIAL PRIMARY KEY,
    asset_id VARCHAR(50) REFERENCES assets(id),  -- Changed to alphanumeric
    work_order_id INTEGER,
    performed_by INTEGER REFERENCES users(id),
    performed_at TIMESTAMP,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_maintenance_asset ON maintenance_history(asset_id);

CREATE TABLE IF NOT EXISTS inspections (
    id SERIAL PRIMARY KEY,
    asset_id VARCHAR(50) REFERENCES assets(id),  -- Changed to alphanumeric
    asset_code VARCHAR(100),
    inspector VARCHAR(200),
    inspection_date DATE,
    finding_type VARCHAR(100),
    observations TEXT,
    risk_level VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inspections_asset ON inspections(asset_id);

CREATE TABLE IF NOT EXISTS asset_readings (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id),
    asset_id VARCHAR(50) REFERENCES assets(id),  -- Changed to alphanumeric
    reading_type VARCHAR(100),
    value DECIMAL(12,4),
    unit VARCHAR(50),
    recorded_at TIMESTAMP DEFAULT NOW(),
    anomaly_flag BOOLEAN DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_readings_asset ON asset_readings(asset_id);

CREATE TABLE IF NOT EXISTS certificates (
    certificate_id SERIAL PRIMARY KEY,
    asset_id VARCHAR(50) REFERENCES assets(id),  -- Changed to alphanumeric
    certificate_type VARCHAR(100),
    certificate_name VARCHAR(200),
    expiry_date DATE,
    status VARCHAR(50) DEFAULT 'valid',
    uploaded_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_certificates_asset ON certificates(asset_id);

CREATE TABLE IF NOT EXISTS known_issues (
    id SERIAL PRIMARY KEY,
    manufacturer VARCHAR(100),
    model VARCHAR(100),
    issue_description TEXT,
    occurrence_count INTEGER DEFAULT 1,
    avg_repair_cost DECIMAL(10,2),
    solutions JSONB,
    last_occurred TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_known_issues_model ON known_issues(manufacturer, model);

-- ============================================================================
-- AUTO-UPDATE TRIGGERS
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER work_orders_updated
    BEFORE UPDATE ON work_orders
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER assets_updated
    BEFORE UPDATE ON assets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER journey_logs_updated
    BEFORE UPDATE ON wo_journey_logs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- SAMPLE DATA - 150+ INTERCONNECTED RECORDS WITH ALPHANUMERIC IDs
-- ============================================================================

-- Countries (5)
INSERT INTO countries (name, code) VALUES
('United Arab Emirates', 'UAE'),
('United States', 'USA'),
('United Kingdom', 'UK'),
('India', 'IND'),
('Saudi Arabia', 'SAU');

-- Organization (1)
INSERT INTO organizations (name, code, timezone) VALUES
('TechCorp Facilities LLC', 'TC-FMGMT', 'Asia/Dubai');

-- Users (15)
INSERT INTO users (organization_id, email, full_name, role) VALUES
(1, 'john.smith@techcorp.ae', 'John Smith', 'Requester'),
(1, 'sarah.johnson@techcorp.ae', 'Sarah Johnson', 'Facility Manager'),
(1, 'mike.technician@techcorp.ae', 'Mike Johnson', 'Senior Technician'),
(1, 'ahmed.khan@techcorp.ae', 'Ahmed Khan', 'HVAC Specialist'),
(1, 'lisa.martinez@techcorp.ae', 'Lisa Martinez', 'Electrical Engineer'),
(1, 'omar.hassan@techcorp.ae', 'Omar Hassan', 'Plumbing Specialist'),
(1, 'david.approver@techcorp.ae', 'David Wilson', 'Operations Manager'),
(1, 'maria.inspector@techcorp.ae', 'Maria Rodriguez', 'Quality Inspector'),
(1, 'khalid.vendor@techcorp.ae', 'Khalid Al-Mansoori', 'Vendor Coordinator'),
(1, 'jennifer.admin@techcorp.ae', 'Jennifer Lee', 'System Administrator'),
(1, 'robert.tech@techcorp.ae', 'Robert Brown', 'Maintenance Tech'),
(1, 'fatima.engineer@techcorp.ae', 'Fatima Ali', 'Mechanical Engineer'),
(1, 'carlos.supervisor@techcorp.ae', 'Carlos Garcia', 'Maintenance Supervisor'),
(1, 'aisha.planner@techcorp.ae', 'Aisha Mohammed', 'Maintenance Planner'),
(1, 'thomas.director@techcorp.ae', 'Thomas Anderson', 'Facilities Director');

-- Sites (Alphanumeric IDs)
INSERT INTO sites (site_id, organization_id, site_name, site_code, city, country, timezone, status) VALUES
('SITE-DXB-001', 1, 'Dubai Main Campus', 'DXB-MAIN', 'Dubai', 'UAE', 'Asia/Dubai', 'active'),
('SITE-DXB-002', 1, 'Dubai Silicon Oasis', 'DXB-DSO', 'Dubai', 'UAE', 'Asia/Dubai', 'active'),
('SITE-AUH-001', 1, 'Abu Dhabi Office', 'AUH-OFF', 'Abu Dhabi', 'UAE', 'Asia/Dubai', 'active');

-- Locations (10)
INSERT INTO locations (organization_id, site_id, name, type, level, address, city, province, country, timezone) VALUES
(1, 'SITE-DXB-001', 'Main Campus', 'Campus', 1, 'Dubai Internet City', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai'),
(1, 'SITE-DXB-001', 'Building A', 'Building', 2, 'Dubai Internet City - Plot A', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai'),
(1, 'SITE-DXB-001', 'Building B', 'Building', 2, 'Dubai Internet City - Plot B', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai'),
(1, 'SITE-DXB-001', 'Building C', 'Building', 2, 'Dubai Internet City - Plot C', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai'),
(1, 'SITE-DXB-001', 'Building A - Roof Level', 'Floor', 3, 'Building A Roof', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai'),
(1, 'SITE-DXB-001', 'Building A - Basement 1', 'Floor', 3, 'Building A B1', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai'),
(1, 'SITE-DXB-001', 'Building B - Basement 2', 'Floor', 3, 'Building B B2', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai'),
(1, 'SITE-DXB-001', 'Building C - Lobby', 'Floor', 3, 'Building C Ground', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai'),
(1, 'SITE-DXB-001', 'Mechanical Room A1', 'Room', 4, 'Building A B1 Mechanical', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai'),
(1, 'SITE-DXB-001', 'Generator Room', 'Room', 4, 'Building A B1 Generator', 'Dubai', 'Dubai', 'UAE', 'Asia/Dubai');

UPDATE locations SET parent_location_id = 1 WHERE id IN (2, 3, 4);
UPDATE locations SET parent_location_id = 2 WHERE id IN (5, 6);
UPDATE locations SET parent_location_id = 3 WHERE id = 7;
UPDATE locations SET parent_location_id = 4 WHERE id = 8;
UPDATE locations SET parent_location_id = 6 WHERE id IN (9, 10);

-- Asset Categories (5)
INSERT INTO asset_categories (organization_id, category_name) VALUES
(1, 'HVAC'),
(1, 'Electrical'),
(1, 'Plumbing'),
(1, 'Elevators'),
(1, 'Fire Safety');

-- Assets (15 with Alphanumeric IDs)
INSERT INTO assets (id, organization_id, site_id, location_id, category_id, asset_name, asset_code, manufacturer, model, 
    serial_number, installation_date, warranty_expiry, status, health_score, criticality, location) VALUES
('AST-HVAC-301', 1, 'SITE-DXB-001', 5, 1, 'Rooftop Unit #3', 'HVAC-301', 'Carrier', '50TC-030', 'SN-12345-ABC', 
    '2018-03-15', '2023-03-15', 'active', 85, 'high', 'Building A, Roof Level'),
('AST-CHILLER-102', 1, 'SITE-DXB-001', 7, 1, 'Central Chiller Unit #2', 'CHILLER-102', 'Trane', 'CVHE-500', 'TR-78901-XYZ', 
    '2020-06-01', '2025-06-01', 'active', 92, 'critical', 'Building B, Basement 2'),
('AST-PUMP-205', 1, 'SITE-DXB-001', 9, 3, 'Main Water Pump #5', 'PUMP-205', 'Grundfos', 'CR-150', 'GR-45678-DEF', 
    '2019-09-10', '2024-09-10', 'active', 88, 'high', 'Mechanical Room A1'),
('AST-ELEV-401', 1, 'SITE-DXB-001', 8, 4, 'Passenger Elevator #1', 'ELEV-401', 'Otis', 'Gen2-Comfort', 'OT-23456-GHI', 
    '2021-01-20', '2026-01-20', 'active', 95, 'critical', 'Building C, Lobby'),
('AST-GEN-501', 1, 'SITE-DXB-001', 10, 2, 'Emergency Generator #1', 'GEN-501', 'Cummins', 'C500D5', 'CM-67890-JKL', 
    '2017-11-05', '2022-11-05', 'active', 78, 'critical', 'Generator Room'),
('AST-HVAC-101', 1, 'SITE-DXB-001', 5, 1, 'Rooftop Unit #1', 'HVAC-101', 'Carrier', '50TC-025', 'SN-11111-AAA', 
    '2017-05-10', '2022-05-10', 'active', 82, 'high', 'Building A, Roof Level'),
('AST-HVAC-201', 1, 'SITE-DXB-001', 5, 1, 'Rooftop Unit #2', 'HVAC-201', 'Carrier', '50TC-030', 'SN-22222-BBB', 
    '2018-06-15', '2023-06-15', 'active', 87, 'high', 'Building A, Roof Level'),
('AST-FIRE-101', 1, 'SITE-DXB-001', 6, 5, 'Fire Suppression System', 'FIRE-101', 'Tyco', 'TFS-500', 'TY-33333-CCC', 
    '2019-03-20', '2024-03-20', 'active', 94, 'critical', 'Building A, Basement 1'),
('AST-CHILLER-101', 1, 'SITE-DXB-001', 7, 1, 'Chiller Unit #1', 'CHILLER-101', 'Trane', 'CVHE-450', 'TR-44444-DDD', 
    '2019-08-10', '2024-08-10', 'active', 89, 'critical', 'Building B, Basement 2'),
('AST-ELEC-201', 1, 'SITE-DXB-001', 2, 2, 'Main Electrical Panel A', 'ELEC-201', 'Schneider', 'PowerPact', 'SC-55555-EEE', 
    '2020-02-15', '2025-02-15', 'active', 96, 'critical', 'Building A'),
('AST-ELEC-202', 1, 'SITE-DXB-001', 3, 2, 'Main Electrical Panel B', 'ELEC-202', 'Schneider', 'PowerPact', 'SC-66666-FFF', 
    '2020-02-15', '2025-02-15', 'active', 95, 'critical', 'Building B'),
('AST-PUMP-101', 1, 'SITE-DXB-001', 9, 3, 'Domestic Water Pump #1', 'PUMP-101', 'Grundfos', 'CR-100', 'GR-77777-GGG', 
    '2018-07-20', '2023-07-20', 'active', 86, 'high', 'Mechanical Room A1'),
('AST-PUMP-102', 1, 'SITE-DXB-001', 9, 3, 'Domestic Water Pump #2', 'PUMP-102', 'Grundfos', 'CR-100', 'GR-88888-HHH', 
    '2018-07-20', '2023-07-20', 'active', 85, 'high', 'Mechanical Room A1'),
('AST-ELEV-402', 1, 'SITE-DXB-001', 8, 4, 'Passenger Elevator #2', 'ELEV-402', 'Otis', 'Gen2-Comfort', 'OT-99999-III', 
    '2021-01-20', '2026-01-20', 'active', 94, 'critical', 'Building C, Lobby'),
('AST-UPS-101', 1, 'SITE-DXB-001', 10, 2, 'UPS System #1', 'UPS-101', 'APC', 'Smart-UPS', 'APC-00000-JJJ', 
    '2020-09-15', '2025-09-15', 'active', 91, 'critical', 'Generator Room');

-- Vendors (5 with Alphanumeric IDs)
INSERT INTO vendors (id, organization_id, vendor_name, vendor_code, address, city, phone, email, status) VALUES
('VEN-TCOOL-001', 1, 'TechCool HVAC Services', 'V-TCOOL', 'Industrial Area 5, Dubai', 'Dubai', '+971-4-1234567', 'info@techcool.ae', 'active'),
('VEN-CLIMATE-001', 1, 'Climate Solutions LLC', 'V-CLIMATE', 'Al Quoz, Dubai', 'Dubai', '+971-4-2345678', 'service@climate-sol.ae', 'active'),
('VEN-ABCMECH-001', 1, 'ABC Mechanical Contracting', 'V-ABCMECH', 'Jebel Ali, Dubai', 'Dubai', '+971-4-3456789', 'support@abcmech.ae', 'active'),
('VEN-EPRO-001', 1, 'ElectroPro Engineering', 'V-EPRO', 'Dubai Silicon Oasis', 'Dubai', '+971-4-4567890', 'contact@electropro.ae', 'active'),
('VEN-PWORKS-001', 1, 'PlumbWorks Services', 'V-PWORKS', 'Dubai Investment Park', 'Dubai', '+971-4-5678901', 'help@plumbworks.ae', 'active');

-- Vendor Contracts (3)
INSERT INTO vendor_contracts (organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, sla_terms, status) VALUES
(1, 'VEN-TCOOL-001', 'Annual HVAC Maintenance Contract 2024', '2024-01-01', '2024-12-31', 250000.00, '4-hour response time for critical issues', 'active'),
(1, 'VEN-EPRO-001', 'Electrical Preventive Maintenance', '2024-01-01', '2024-12-31', 180000.00, '2-hour response for power failures', 'active'),
(1, 'VEN-PWORKS-001', 'Plumbing Emergency Services', '2024-01-01', '2024-12-31', 120000.00, '3-hour response time', 'active');

-- Technicians (5)
INSERT INTO technicians (organization_id, user_id, base_location, availability_status, performance_score) VALUES
(1, 3, 2, 'available', 95),
(1, 4, 2, 'available', 92),
(1, 5, 3, 'available', 88),
(1, 6, 2, 'available', 90),
(1, 11, 4, 'available', 87);

-- Technician Skills (25)
INSERT INTO technician_skills (technician_id, skill_name, skill_level) VALUES
(1, 'HVAC Systems', 'expert'),
(1, 'Refrigeration', 'expert'),
(1, 'Mechanical Repair', 'advanced'),
(1, 'Belt Replacement', 'expert'),
(1, 'Fall Protection', 'certified'),
(2, 'HVAC Systems', 'expert'),
(2, 'Chiller Maintenance', 'expert'),
(2, 'Refrigeration', 'expert'),
(2, 'Electrical Systems', 'intermediate'),
(2, 'Fall Protection', 'certified'),
(3, 'Electrical Systems', 'expert'),
(3, 'Power Distribution', 'expert'),
(3, 'Transformer Maintenance', 'advanced'),
(3, 'Safety Lockout', 'certified'),
(3, 'Arc Flash Protection', 'certified'),
(4, 'Plumbing Systems', 'expert'),
(4, 'Pump Maintenance', 'expert'),
(4, 'Water Treatment', 'advanced'),
(4, 'Backflow Prevention', 'certified'),
(5, 'General Maintenance', 'advanced'),
(5, 'HVAC Systems', 'intermediate'),
(5, 'Electrical Systems', 'intermediate'),
(5, 'Plumbing Systems', 'intermediate'),
(5, 'Preventive Maintenance', 'advanced'),
(5, 'Safety Compliance', 'certified');

-- Spare Parts (10)
INSERT INTO spare_parts (organization_id, part_name, part_code, description, unit_price, stock_quantity, unit_of_measure, supplier_id) VALUES
(1, 'HVAC Bearing P-50TC-BRG-001', 'P-BRG-001', 'Replacement bearing for Carrier 50TC units', 450.00, 0, 'EA', 'VEN-TCOOL-001'),
(1, 'HVAC Belt P-50TC-BLT-002', 'P-BLT-002', 'V-belt for Carrier 50TC units', 85.00, 3, 'EA', 'VEN-TCOOL-001'),
(1, 'HVAC Filter 24x24x2', 'P-FLT-001', 'Standard air filter 24x24x2', 45.00, 25, 'EA', 'VEN-TCOOL-001'),
(1, 'Refrigerant R410A (25lb)', 'P-REF-R410A', 'R410A refrigerant cylinder', 380.00, 8, 'EA', 'VEN-TCOOL-001'),
(1, 'Chiller Compressor Oil', 'P-OIL-CH01', 'Synthetic compressor oil for chillers', 220.00, 15, 'GAL', 'VEN-TCOOL-001'),
(1, 'Pump Seal Kit GR-CR150', 'P-SEAL-001', 'Mechanical seal kit for Grundfos CR-150', 195.00, 5, 'EA', 'VEN-PWORKS-001'),
(1, 'Pump Impeller GR-CR150', 'P-IMP-001', 'Replacement impeller for CR-150', 320.00, 2, 'EA', 'VEN-PWORKS-001'),
(1, 'Electrical Contactor 3-Phase', 'P-CONT-001', '100A 3-phase contactor', 165.00, 8, 'EA', 'VEN-EPRO-001'),
(1, 'Circuit Breaker 100A', 'P-CB-100A', '100A circuit breaker', 125.00, 12, 'EA', 'VEN-EPRO-001'),
(1, 'Generator Oil Filter', 'P-GEN-FLT', 'Oil filter for Cummins C500D5', 75.00, 10, 'EA', NULL);

-- PPM Schedules (5)
INSERT INTO ppm_schedules (asset_id, asset_name, location, task_description, task_type, frequency, priority, 
    estimated_duration_minutes, required_skills, active) VALUES
('AST-HVAC-301', 'Rooftop Unit #3', 'Building A, Roof Level', 'Quarterly HVAC maintenance and filter change', 
    'Preventive Maintenance', 'quarterly', 'medium', 180, ARRAY['HVAC Systems', 'Fall Protection'], true),
('AST-CHILLER-102', 'Central Chiller #2', 'Building B, Basement 2', 'Monthly chiller inspection and oil check', 
    'Preventive Maintenance', 'monthly', 'high', 240, ARRAY['HVAC Systems', 'Chiller Maintenance'], true),
('AST-PUMP-205', 'Main Water Pump #5', 'Mechanical Room A1', 'Bi-annual pump service and seal check', 
    'Preventive Maintenance', 'semi-annual', 'medium', 120, ARRAY['Plumbing Systems', 'Pump Maintenance'], true),
('AST-GEN-501', 'Emergency Generator #1', 'Generator Room', 'Monthly generator load test', 
    'Preventive Maintenance', 'monthly', 'critical', 60, ARRAY['Electrical Systems', 'Generator Service'], true),
('AST-FIRE-101', 'Fire Suppression System', 'Building A, Basement 1', 'Quarterly fire system inspection', 
    'Preventive Maintenance', 'quarterly', 'critical', 90, ARRAY['Fire Safety', 'System Testing'], true);

-- Maintenance History (10)
INSERT INTO maintenance_history (asset_id, performed_by, performed_at, notes) VALUES
('AST-HVAC-301', 3, '2024-01-15 10:00:00', 'Replaced air filters, checked refrigerant levels'),
('AST-HVAC-301', 3, '2024-02-20 14:30:00', 'Belt adjustment and tension check'),
('AST-HVAC-301', 3, '2024-03-18 09:15:00', 'Motor bearing lubrication'),
('AST-CHILLER-102', 4, '2024-01-10 11:00:00', 'Oil change and pressure test'),
('AST-CHILLER-102', 4, '2024-02-10 10:30:00', 'Monthly inspection - all systems normal'),
('AST-PUMP-205', 6, '2023-09-15 13:00:00', 'Pump seal replacement'),
('AST-PUMP-205', 6, '2024-01-22 15:00:00', 'Impeller cleaning and alignment check'),
('AST-GEN-501', 5, '2024-01-08 08:00:00', 'Monthly load test - passed'),
('AST-GEN-501', 5, '2024-02-08 08:00:00', 'Monthly load test - passed'),
('AST-GEN-501', 5, '2024-03-08 08:00:00', 'Monthly load test - passed');

-- Known Issues (3)
INSERT INTO known_issues (manufacturer, model, issue_description, occurrence_count, avg_repair_cost, solutions) VALUES
('Carrier', '50TC-030', 'Motor bearing failure due to lack of lubrication', 8, 2500.00, 
    '{"solution": "Replace bearing and implement monthly lubrication schedule", "parts": ["P-BRG-001"], "labor_hours": 4}'),
('Grundfos', 'CR-150', 'Seal leak after 5 years of operation', 12, 850.00, 
    '{"solution": "Replace mechanical seal kit", "parts": ["P-SEAL-001"], "labor_hours": 2}'),
('Cummins', 'C500D5', 'Battery failure in hot climate', 5, 1200.00, 
    '{"solution": "Replace battery and check charging system", "parts": ["Battery-12V-200AH"], "labor_hours": 1}');

-- Certificates (5)
INSERT INTO certificates (asset_id, certificate_type, certificate_name, expiry_date, status) VALUES
('AST-GEN-501', 'roof_access_permit', 'Building A Roof Access Permit 2024', '2024-05-15', 'valid'),
('AST-HVAC-301', 'roof_access_permit', 'Building A Roof Access Permit 2024', '2024-05-15', 'valid'),
('AST-FIRE-101', 'fire_safety_inspection', 'Fire Safety Compliance Certificate', '2024-12-31', 'valid'),
('AST-ELEV-401', 'elevator_inspection', 'Elevator Safety Inspection Certificate', '2024-06-30', 'valid'),
('AST-ELEV-402', 'elevator_inspection', 'Elevator Safety Inspection Certificate', '2024-06-30', 'valid');

-- Asset Readings (10)
INSERT INTO asset_readings (organization_id, asset_id, reading_type, value, unit, recorded_at, anomaly_flag) VALUES
(1, 'AST-HVAC-301', 'vibration', 2.5, 'mm/s', '2024-04-25 09:00:00', true),
(1, 'AST-HVAC-301', 'temperature_supply', 55.0, 'F', '2024-04-25 09:00:00', false),
(1, 'AST-HVAC-301', 'temperature_return', 78.0, 'F', '2024-04-25 09:00:00', false),
(1, 'AST-CHILLER-102', 'pressure', 185.0, 'PSI', '2024-04-20 10:00:00', false),
(1, 'AST-CHILLER-102', 'temperature_chilled_water', 42.0, 'F', '2024-04-20 10:00:00', false),
(1, 'AST-PUMP-205', 'pressure_discharge', 95.0, 'PSI', '2024-04-22 11:00:00', false),
(1, 'AST-PUMP-205', 'flow_rate', 850.0, 'GPM', '2024-04-22 11:00:00', false),
(1, 'AST-GEN-501', 'voltage_output', 415.0, 'V', '2024-04-08 08:30:00', false),
(1, 'AST-GEN-501', 'frequency', 50.0, 'Hz', '2024-04-08 08:30:00', false),
(1, 'AST-GEN-501', 'load_percentage', 85.0, '%', '2024-04-08 08:30:00', false);

-- Inspections (3)
INSERT INTO inspections (asset_id, asset_code, inspector, inspection_date, finding_type, observations, risk_level) VALUES
('AST-HVAC-301', 'HVAC-301', 'Maria Rodriguez', '2024-04-20', 'Equipment Condition', 
    'Unusual vibration and grinding noise detected from motor bearing', 'high'),
('AST-CHILLER-102', 'CHILLER-102', 'Maria Rodriguez', '2024-04-15', 'Routine Inspection', 
    'All systems operating within normal parameters', 'low'),
('AST-GEN-501', 'GEN-501', 'Carlos Garcia', '2024-04-08', 'Load Test', 
    'Generator performed load test successfully, battery voltage stable', 'low');

-- Work Orders with Full Intelligence (5)
INSERT INTO work_orders (
    work_order_id, organization_id, site_id, asset_id, asset, asset_code, location_id, location,
    title, description, issue_description, priority, status, work_order_type, maintenance_type,
    source, source_reference, requester_name, requester_email, requested_by_id,
    scheduled_date, scheduled_time, estimated_hours, approval_type, assigned_technician, assigned_vendor,
    criticality, safety, compliance, asset_intelligence, spare_parts, parts_order_status,
    suggested_vendors, resource_allocation, schedule_constraints,
    created_by, created_at
) VALUES (
    'WO-20240427093000', 1, 'SITE-DXB-001', 'AST-HVAC-301', 'Rooftop Unit #3', 'HVAC-301', 5, 'Building A, Roof Level',
    'HVAC Unit Making Grinding Noise - High Priority',
    'HVAC unit making loud grinding noise and reduced cooling capacity. Needs immediate attention.',
    'HVAC unit making loud grinding noise and reduced cooling capacity',
    'high', 'pending_approval', 'Corrective', 'Emergency Repair',
    'email', 'email-20240427-001', 'John Smith', 'john.smith@techcorp.ae', 1,
    '2024-05-02', '18:00:00', 4.0, 'preparation', 1, 'VEN-TCOOL-001',
    '{"safety_score": 65, "operational_score": 78, "financial_score": 72, "compliance_score": 45, 
      "overall_score": 65, "level": "HIGH", "response_time_hours": 24}',
    '{"safety_conditions": ["fall_protection", "hazardous_materials", "lockout_tagout"],
      "permits_required": ["roof_access_permit", "fall_protection_plan"],
      "ppe_required": ["safety_harness", "hard_hat", "safety_glasses", "gloves"]}',
    '{"energy_tracking": true, "environmental_tracking": true, "refrigerant_handling": true}',
    '{"age_years": 6.1, "warranty_status": "expired", "similar_failures_count": 8, "mtbf_days": 180, "avg_repair_cost": 2500}',
    '{"parts": [
        {"part_id": 1, "part_code": "P-BRG-001", "quantity": 1, "unit_price": 450.00, "in_stock": false},
        {"part_id": 2, "part_code": "P-BLT-002", "quantity": 1, "unit_price": 85.00, "in_stock": true}
      ], "total_parts_cost": 535.00}',
    '{"bearing_status": "out_of_stock", "purchase_order": "PO-2024-1234", "expected_delivery": "2024-05-02"}',
    '{"top_vendors": [
        {"vendor_id": "VEN-TCOOL-001", "name": "TechCool HVAC", "score": 94.25},
        {"vendor_id": "VEN-CLIMATE-001", "name": "Climate Solutions", "score": 87.85}
      ]}',
    '{"technician_id": 1, "technician_name": "Mike Johnson", "score": 94.85}',
    '{"suggested_date": "2024-05-02", "suggested_time": "18:00", "duration_hours": 4}',
    1, '2024-04-27 09:30:00'
);

-- Journey Log
INSERT INTO wo_journey_logs (
    jlog_id, work_order_id, status, current_step, asset_id, assigned_technician_id,
    estimated_cost, milestones, expected_timeline, created_at
) VALUES (
    'JLOG-20240427093000-EML', 'WO-20240427093000', 'initiated', '15', 'AST-HVAC-301', '1', 3035.00,
    '[
        {"step": 1, "name": "Work Order Created", "status": "completed"},
        {"step": 2, "name": "Workspace Queries", "status": "completed"},
        {"step": 3, "name": "Criticality Assessed", "status": "completed"},
        {"step": 4, "name": "Safety Review", "status": "completed"},
        {"step": 5, "name": "Compliance Check", "status": "completed"},
        {"step": 6, "name": "Location Validated", "status": "completed"},
        {"step": 7, "name": "Asset Intelligence", "status": "completed"},
        {"step": 8, "name": "Clearance Verified", "status": "completed"},
        {"step": 9, "name": "Parts Identified", "status": "completed"},
        {"step": 10, "name": "Inventory Checked", "status": "completed"},
        {"step": 11, "name": "Vendors Scored", "status": "completed"},
        {"step": 12, "name": "Resource Allocated", "status": "completed"},
        {"step": 13, "name": "Schedule Optimized", "status": "completed"},
        {"step": 14, "name": "Workspace Pin Created", "status": "completed"},
        {"step": 15, "name": "Journey Initiated", "status": "completed"}
    ]',
    '{"total_steps": 15, "started_at": "2024-04-27T09:30:00Z"}',
    '2024-04-27 09:30:00'
);

-- Additional Work Orders (4 more)
INSERT INTO work_orders (
    work_order_id, organization_id, site_id, asset_id, asset, asset_code, location_id, location,
    title, description, task_description, priority, status, work_order_type, maintenance_type,
    source, requester_name, requested_by_id, scheduled_date, estimated_hours, approval_type,
    assigned_technician, created_by, created_at
) VALUES 
('WO-20240425140000', 1, 'SITE-DXB-001', 'AST-CHILLER-102', 'Central Chiller #2', 'CHILLER-102', 7, 'Building B, Basement 2',
    'Monthly Chiller Maintenance', 'Scheduled monthly preventive maintenance',
    'Monthly inspection, oil check, pressure test', 'medium', 'preparing', 'Preventive', 'Planned Maintenance',
    'ppm_schedule', 'Aisha Mohammed', 14, '2024-05-10', 4.0, 'simple', 2, 14, '2024-04-25 14:00:00'),
('WO-20240426153000', 1, 'SITE-DXB-001', 'AST-PUMP-205', 'Main Water Pump #5', 'PUMP-205', 9, 'Mechanical Room A1',
    'Water Pump Seal Replacement', 'Preventive seal replacement',
    'Replace seal kit before major failure', 'medium', 'prepared', 'Corrective', 'Preventive Replacement',
    'inspection', 'Omar Hassan', 6, '2024-04-30', 2.0, 'simple', 4, 2, '2024-04-26 15:30:00'),
('WO-20240420100000', 1, 'SITE-DXB-001', 'AST-ELEV-401', 'Passenger Elevator #1', 'ELEV-401', 8, 'Building C, Lobby',
    'Quarterly Elevator Safety Inspection', 'Required quarterly safety inspection',
    'Load test, brake test, emergency systems check', 'high', 'active', 'Inspection', 'Compliance',
    'compliance_schedule', 'Carlos Garcia', 13, '2024-04-28', 3.0, 'simple', 5, 13, '2024-04-20 10:00:00'),
('WO-20240401080000', 1, 'SITE-DXB-001', 'AST-GEN-501', 'Emergency Generator #1', 'GEN-501', 10, 'Generator Room',
    'Monthly Generator Load Test', 'Monthly load test',
    'Perform 1-hour load test at 85% capacity', 'critical', 'completed', 'Preventive', 'Testing',
    'ppm_schedule', 'Aisha Mohammed', 14, '2024-04-08', 1.0, 'simple', 3, 14, '2024-04-01 08:00:00');

-- Status History (10)
INSERT INTO wo_status_history (work_order_id, from_status, to_status, changed_by, notes, changed_at) VALUES
('WO-20240427093000', NULL, 'pending_approval', 'John Smith', 'Work order created from email', '2024-04-27 09:30:00'),
('WO-20240425140000', 'pending_approval', 'preparing', 'Aisha Mohammed', 'PPM approved, preparing resources', '2024-04-25 14:00:00'),
('WO-20240426153000', 'preparing', 'prepared', 'Sarah Johnson', 'Parts sourced, ready for execution', '2024-04-26 15:30:00'),
('WO-20240420100000', 'pending_approval', 'preparing', 'Carlos Garcia', 'Compliance inspection approved', '2024-04-20 10:30:00'),
('WO-20240420100000', 'prepared', 'active', 'Lisa Martinez', 'Technician on-site, work started', '2024-04-28 09:00:00'),
('WO-20240401080000', 'pending_approval', 'preparing', 'Aisha Mohammed', 'Monthly PPM approved', '2024-04-01 08:00:00'),
('WO-20240401080000', 'prepared', 'active', 'Mike Johnson', 'Started load test', '2024-04-08 08:00:00'),
('WO-20240401080000', 'active', 'completed', 'Mike Johnson', 'Load test passed', '2024-04-08 09:00:00');

-- Approval Requests (3)
INSERT INTO wo_approval_requests (request_id, work_order_id, approval_type, approver, status, requested_at) VALUES
('APR-20240427093000', 'WO-20240427093000', 'preparation', 'David Wilson', 'pending', '2024-04-27 09:31:00'),
('APR-20240425140000', 'WO-20240425140000', 'simple', 'David Wilson', 'approved', '2024-04-25 14:01:00'),
('APR-20240426153000', 'WO-20240426153000', 'simple', 'David Wilson', 'approved', '2024-04-26 15:31:00');

-- Work Order Parts (2)
INSERT INTO work_order_parts (work_order_id, part_id, quantity_used, unit_cost, asset_id) VALUES
('WO-20240426153000', 6, 1, 195.00, 'AST-PUMP-205'),
('WO-20240401080000', 10, 1, 75.00, 'AST-GEN-501');

-- ============================================================================
-- SUMMARY & VERIFICATION
-- ============================================================================

-- Count all records
SELECT 
    'sites' as table_name, COUNT(*) as record_count FROM sites
UNION ALL SELECT 'assets', COUNT(*) FROM assets
UNION ALL SELECT 'vendors', COUNT(*) FROM vendors
UNION ALL SELECT 'work_orders', COUNT(*) FROM work_orders
UNION ALL SELECT 'spare_parts', COUNT(*) FROM spare_parts
UNION ALL SELECT 'technicians', COUNT(*) FROM technicians
ORDER BY table_name;

-- Show alphanumeric IDs in use
(SELECT 'Asset IDs' as id_type, id FROM assets LIMIT 5)
UNION ALL
(SELECT 'Vendor IDs', id FROM vendors LIMIT 5)
UNION ALL
(SELECT 'Site IDs', site_id FROM sites LIMIT 3);

SELECT '✅ Database created with ALPHANUMERIC IDs for Assets, Sites, and Vendors!' as status;

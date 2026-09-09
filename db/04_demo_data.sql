-- A demo portfolio for a new environment: buildings, compliance, vendors and accounts.
--
-- Invented. No customer data is in this file and none should ever be added to it — the
-- real records live in whatever database they were imported into, and a repository is the
-- wrong place for a customer's compliance evidence.
--
-- Idempotent: every id is derived from a stable name, so loading this twice changes
-- nothing. Dates are anchored to 2026-01-01 rather than to now(), so the file is the same
-- on the day it is generated and the day it is read.
--
--   psql -U cafm -d hoistra -v ON_ERROR_STOP=1 -f 04_demo_data.sql
--
-- Load it after 03_bootstrap.sql, which creates the organisation everything hangs off.

SET search_path TO plenum_cafm, public;
BEGIN;

-- ── sites: the estate record each building inherits its address and metering from ──
INSERT INTO sites (site_id, site_name, building_name, building_code, site_code,
        country, country_code, state, city, site_type, use_type, floors, gfa_sqm,
        metering_route, metering_granularity, benchmark_standard, eui_kwh_per_m2,
        benchmark_kwh_per_m2, hoist_score, status)
    VALUES ('B-001', 'Bishopsgate Tower', 'Bishopsgate Tower', 'B-001', 'B-001', 'United Kingdom', 'UK',
        'Greater London', 'London', 'Commercial', 'Commercial', '34', '38276',
        'HH data collector · LoA', 'sub-metered', 'CIBSE TM46', 214, 215, 88, 'active')
    ON CONFLICT (site_id) DO NOTHING;
INSERT INTO sites (site_id, site_name, building_name, building_code, site_code,
        country, country_code, state, city, site_type, use_type, floors, gfa_sqm,
        metering_route, metering_granularity, benchmark_standard, eui_kwh_per_m2,
        benchmark_kwh_per_m2, hoist_score, status)
    VALUES ('B-002', 'Kingsway House', 'Kingsway House', 'B-002', 'B-002', 'United Kingdom', 'UK',
        'Greater London', 'London', 'Mixed', 'Mixed', '11', '13750',
        'HH data collector · LoA', 'sub-metered', 'CIBSE TM46', 198, 172, 71, 'active')
    ON CONFLICT (site_id) DO NOTHING;
INSERT INTO sites (site_id, site_name, building_name, building_code, site_code,
        country, country_code, state, city, site_type, use_type, floors, gfa_sqm,
        metering_route, metering_granularity, benchmark_standard, eui_kwh_per_m2,
        benchmark_kwh_per_m2, hoist_score, status)
    VALUES ('B-003', 'Town Hall', 'Town Hall', 'B-003', 'B-003', 'United Kingdom', 'UK',
        'Greater Manchester', 'Manchester', 'Commercial', 'Commercial', '6', '8919',
        'HH data collector · LoA', 'building-level', 'CIBSE TM46', 231, 215, 62, 'active')
    ON CONFLICT (site_id) DO NOTHING;
INSERT INTO sites (site_id, site_name, building_name, building_code, site_code,
        country, country_code, state, city, site_type, use_type, floors, gfa_sqm,
        metering_route, metering_granularity, benchmark_standard, eui_kwh_per_m2,
        benchmark_kwh_per_m2, hoist_score, status)
    VALUES ('B-004', 'Meridian Quay', 'Meridian Quay', 'B-004', 'B-004', 'United Kingdom', 'UK',
        'Scotland', 'Edinburgh', 'Residential', 'Residential', '22', '17280',
        'SMETS2 · SEC intermediary', 'building-level', 'CIBSE TM46', 164, 150, 84, 'active')
    ON CONFLICT (site_id) DO NOTHING;
INSERT INTO sites (site_id, site_name, building_name, building_code, site_code,
        country, country_code, state, city, site_type, use_type, floors, gfa_sqm,
        metering_route, metering_granularity, benchmark_standard, eui_kwh_per_m2,
        benchmark_kwh_per_m2, hoist_score, status)
    VALUES ('B-005', 'AN Other House', 'AN Other House', 'B-005', 'B-005', 'United Kingdom', 'UK',
        'Greater London', 'London', 'Retail', 'Retail', '4', '6689',
        'HH data collector · LoA', 'building-level', 'CIBSE TM46', 209, 165, 79, 'active')
    ON CONFLICT (site_id) DO NOTHING;
INSERT INTO sites (site_id, site_name, building_name, building_code, site_code,
        country, country_code, state, city, site_type, use_type, floors, gfa_sqm,
        metering_route, metering_granularity, benchmark_standard, eui_kwh_per_m2,
        benchmark_kwh_per_m2, hoist_score, status)
    VALUES ('B-006', 'Riverside Court', 'Riverside Court', 'B-006', 'B-006', 'United Kingdom', 'UK',
        'Greater Manchester', 'Manchester', 'Residential', 'Residential', '15', '11241',
        'SMETS2 · SEC intermediary', 'building-level', 'CIBSE TM46', 158, 145, 90, 'active')
    ON CONFLICT (site_id) DO NOTHING;
INSERT INTO sites (site_id, site_name, building_name, building_code, site_code,
        country, country_code, state, city, site_type, use_type, floors, gfa_sqm,
        metering_route, metering_granularity, benchmark_standard, eui_kwh_per_m2,
        benchmark_kwh_per_m2, hoist_score, status)
    VALUES ('B-007', 'Marina Heights', 'Marina Heights', 'B-007', 'B-007', 'UAE', 'AE',
        'Dubai', 'Dubai', 'Hospital', 'Hospital', '8', '18952',
        'Own sub-meters + BMS', 'sub-metered', 'CIBSE TM46', 246, 228, 58, 'active')
    ON CONFLICT (site_id) DO NOTHING;
INSERT INTO sites (site_id, site_name, building_name, building_code, site_code,
        country, country_code, state, city, site_type, use_type, floors, gfa_sqm,
        metering_route, metering_granularity, benchmark_standard, eui_kwh_per_m2,
        benchmark_kwh_per_m2, hoist_score, status)
    VALUES ('B-008', 'Northgate Mall', 'Northgate Mall', 'B-008', 'B-008', 'United States', 'US',
        'New York', 'New York', 'Mixed', 'Mixed', '3', '29543',
        'Green Button CMD · aggregator', 'sub-metered', 'CIBSE TM46', 188, 205, 74, 'active')
    ON CONFLICT (site_id) DO NOTHING;
INSERT INTO sites (site_id, site_name, building_name, building_code, site_code,
        country, country_code, state, city, site_type, use_type, floors, gfa_sqm,
        metering_route, metering_granularity, benchmark_standard, eui_kwh_per_m2,
        benchmark_kwh_per_m2, hoist_score, status)
    VALUES ('B-009', 'Raffles Link', 'Raffles Link', 'B-009', 'B-009', 'Singapore', 'SG',
        'Central Region', 'Singapore', 'Commercial', 'Commercial', '19', '24898',
        'Retailer feed · contracted', 'sub-metered', 'CIBSE TM46', 176, 192, 92, 'active')
    ON CONFLICT (site_id) DO NOTHING;

-- ── buildings: the graph node everything else keys on ──
INSERT INTO buildings (building_id, site_id, name, building_code, primary_use,
        floors, gross_area_sqft, eui_kwh_m2, hoist_score)
    VALUES ('4791b70b-862a-5a75-8d24-5fe13d039131', 'B-001', 'Bishopsgate Tower', 'B-001', 'Commercial'::building_primary_use,
        34, 411999.04, 214, 88)
    ON CONFLICT (building_id) DO NOTHING;
INSERT INTO buildings (building_id, site_id, name, building_code, primary_use,
        floors, gross_area_sqft, eui_kwh_m2, hoist_score)
    VALUES ('7b73cd1a-106f-50fb-a656-135af105df01', 'B-002', 'Kingsway House', 'B-002', 'Mixed'::building_primary_use,
        11, 148003.62, 198, 71)
    ON CONFLICT (building_id) DO NOTHING;
INSERT INTO buildings (building_id, site_id, name, building_code, primary_use,
        floors, gross_area_sqft, eui_kwh_m2, hoist_score)
    VALUES ('425f5590-404f-5d25-ac68-cac5c9cdebcc', 'B-003', 'Town Hall', 'B-003', 'Commercial'::building_primary_use,
        6, 96003.22, 231, 62)
    ON CONFLICT (building_id) DO NOTHING;
INSERT INTO buildings (building_id, site_id, name, building_code, primary_use,
        floors, gross_area_sqft, eui_kwh_m2, hoist_score)
    VALUES ('3e058306-2411-592d-b063-2fb6028e7816', 'B-004', 'Meridian Quay', 'B-004', 'Residential'::building_primary_use,
        22, 186000.19, 164, 84)
    ON CONFLICT (building_id) DO NOTHING;
INSERT INTO buildings (building_id, site_id, name, building_code, primary_use,
        floors, gross_area_sqft, eui_kwh_m2, hoist_score)
    VALUES ('fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'B-005', 'AN Other House', 'B-005', 'Retail'::building_primary_use,
        4, 71999.73, 209, 79)
    ON CONFLICT (building_id) DO NOTHING;
INSERT INTO buildings (building_id, site_id, name, building_code, primary_use,
        floors, gross_area_sqft, eui_kwh_m2, hoist_score)
    VALUES ('f12e9629-95ed-5722-80cc-a1397f627850', 'B-006', 'Riverside Court', 'B-006', 'Residential'::building_primary_use,
        15, 120997.0, 158, 90)
    ON CONFLICT (building_id) DO NOTHING;
INSERT INTO buildings (building_id, site_id, name, building_code, primary_use,
        floors, gross_area_sqft, eui_kwh_m2, hoist_score)
    VALUES ('8f4a8708-a87c-5887-99b5-865c3a98b511', 'B-007', 'Marina Heights', 'B-007', 'Hospital'::building_primary_use,
        8, 203997.43, 246, 58)
    ON CONFLICT (building_id) DO NOTHING;
INSERT INTO buildings (building_id, site_id, name, building_code, primary_use,
        floors, gross_area_sqft, eui_kwh_m2, hoist_score)
    VALUES ('954c3214-8467-528f-95c6-a320de9f579d', 'B-008', 'Northgate Mall', 'B-008', 'Mixed'::building_primary_use,
        3, 317997.9, 188, 74)
    ON CONFLICT (building_id) DO NOTHING;
INSERT INTO buildings (building_id, site_id, name, building_code, primary_use,
        floors, gross_area_sqft, eui_kwh_m2, hoist_score)
    VALUES ('264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'B-009', 'Raffles Link', 'B-009', 'Commercial'::building_primary_use,
        19, 267999.58, 176, 92)
    ON CONFLICT (building_id) DO NOTHING;

-- ── floors and spaces ──
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('3bf55896-9891-5fec-8935-ac047ce416a8', '4791b70b-862a-5a75-8d24-5fe13d039131', 1, 'Level 1', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('bfeb3983-e7d9-5239-ac95-d78202973c93', '3bf55896-9891-5fec-8935-ac047ce416a8', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 1A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('4ebde629-399c-5104-bfa0-e3e42f6d4b92', '4791b70b-862a-5a75-8d24-5fe13d039131', 2, 'Level 2', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('0778025b-29cd-513c-b638-36a6bc9f23b4', '4ebde629-399c-5104-bfa0-e3e42f6d4b92', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 2A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('2d2cb2b7-83fd-5278-8deb-af963973f4d5', '4791b70b-862a-5a75-8d24-5fe13d039131', 3, 'Level 3', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('9f46a54e-90ae-5782-b177-01aaa31fe01a', '2d2cb2b7-83fd-5278-8deb-af963973f4d5', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 3A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('f2166a93-429a-58ca-b5ef-218936420f13', '4791b70b-862a-5a75-8d24-5fe13d039131', 4, 'Level 4', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('01206052-6e4c-5584-b835-5de0d879a4a7', 'f2166a93-429a-58ca-b5ef-218936420f13', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 4A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('7c7a43de-20b2-5761-be9e-47c40546c598', '4791b70b-862a-5a75-8d24-5fe13d039131', 5, 'Level 5', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('ec08bbc9-804c-5c6a-9c9d-338b6babf9ec', '7c7a43de-20b2-5761-be9e-47c40546c598', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 5A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('d1bce7ad-e90b-504c-ad00-4bcb6f2771ae', '4791b70b-862a-5a75-8d24-5fe13d039131', 6, 'Level 6', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('d8572541-118b-5871-b6a8-bfe00154d3fd', 'd1bce7ad-e90b-504c-ad00-4bcb6f2771ae', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 6A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('b7c1b5b5-8309-5d3a-a2c6-36310f06bfdd', '4791b70b-862a-5a75-8d24-5fe13d039131', 7, 'Level 7', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('7af1a83c-d39c-5a54-aabd-924613589230', 'b7c1b5b5-8309-5d3a-a2c6-36310f06bfdd', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 7A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('8702ce91-1db5-56c3-a55f-4ae5b48057b0', '4791b70b-862a-5a75-8d24-5fe13d039131', 8, 'Level 8', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('8b42f726-b535-5948-87b1-e3d5e3bcb068', '8702ce91-1db5-56c3-a55f-4ae5b48057b0', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 8A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('7e1722bd-d976-5626-a6fe-c4210520c0d9', '4791b70b-862a-5a75-8d24-5fe13d039131', 9, 'Level 9', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('9088a9b4-861a-572f-a434-94a7d3b9d187', '7e1722bd-d976-5626-a6fe-c4210520c0d9', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 9A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('2c868790-b0b1-5c6b-bb8e-a857f77ccbf7', '4791b70b-862a-5a75-8d24-5fe13d039131', 10, 'Level 10', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('cfffdc52-c9f7-5b71-99d1-9fecf65d8f0d', '2c868790-b0b1-5c6b-bb8e-a857f77ccbf7', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 10A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('46a3fbc1-5da1-5680-98aa-5ca3a44a8c31', '4791b70b-862a-5a75-8d24-5fe13d039131', 11, 'Level 11', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('89cd8568-2854-5438-bd5a-86f75ad2b269', '46a3fbc1-5da1-5680-98aa-5ca3a44a8c31', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 11A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('5815c8c8-1e31-5659-bbf7-b3658006fdc4', '4791b70b-862a-5a75-8d24-5fe13d039131', 12, 'Level 12', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('d4c7252b-fbb7-59df-a7d5-cc472c677017', '5815c8c8-1e31-5659-bbf7-b3658006fdc4', '4791b70b-862a-5a75-8d24-5fe13d039131', 'Suite 12A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('8c5697b7-12da-5bca-b588-a0311cec100f', '7b73cd1a-106f-50fb-a656-135af105df01', 1, 'Level 1', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('8819949a-2e8a-5d11-9ad1-41564f9bfbe1', '8c5697b7-12da-5bca-b588-a0311cec100f', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 1A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('7319481c-7a2e-5411-a82f-6ea91cd2b98a', '7b73cd1a-106f-50fb-a656-135af105df01', 2, 'Level 2', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('203df175-efeb-5167-8685-33901826cbc6', '7319481c-7a2e-5411-a82f-6ea91cd2b98a', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 2A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('aa1d4a2e-81ac-505d-ad66-10136a719c2a', '7b73cd1a-106f-50fb-a656-135af105df01', 3, 'Level 3', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('a6fe982d-7822-5bf4-ada4-db35f8474a5e', 'aa1d4a2e-81ac-505d-ad66-10136a719c2a', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 3A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('b84d3e67-9f19-5a55-b363-1c517dfe3af4', '7b73cd1a-106f-50fb-a656-135af105df01', 4, 'Level 4', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('69483751-6740-59c4-8e20-745087738106', 'b84d3e67-9f19-5a55-b363-1c517dfe3af4', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 4A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('23cb8bc3-ba12-58fd-906c-d5abe6465d4f', '7b73cd1a-106f-50fb-a656-135af105df01', 5, 'Level 5', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('aceefd9e-5f5d-59aa-84ca-529db6331310', '23cb8bc3-ba12-58fd-906c-d5abe6465d4f', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 5A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('f59da415-49d5-5f1e-90be-960f2863cadf', '7b73cd1a-106f-50fb-a656-135af105df01', 6, 'Level 6', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('0669a565-74aa-5f02-81ff-fab50fd5a0a2', 'f59da415-49d5-5f1e-90be-960f2863cadf', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 6A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('9100c67b-87e9-568a-bc53-bb82782afc04', '7b73cd1a-106f-50fb-a656-135af105df01', 7, 'Level 7', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('63b88861-9982-5273-8b27-fb06460cd1a8', '9100c67b-87e9-568a-bc53-bb82782afc04', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 7A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('82d378d0-e9fc-59bf-bcc9-7abe4edbdede', '7b73cd1a-106f-50fb-a656-135af105df01', 8, 'Level 8', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('350cd0bb-e2a8-53fe-bd75-c2ac186cf8fc', '82d378d0-e9fc-59bf-bcc9-7abe4edbdede', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 8A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('1df5b7b6-623e-5072-b4c0-61883c868f59', '7b73cd1a-106f-50fb-a656-135af105df01', 9, 'Level 9', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('5c463888-cc1e-557b-86d3-2e0d6b744c21', '1df5b7b6-623e-5072-b4c0-61883c868f59', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 9A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('e2ff9d14-ccbf-5f36-bfd1-69ba1e83c1ce', '7b73cd1a-106f-50fb-a656-135af105df01', 10, 'Level 10', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('b1a8457f-6cf9-5e74-b119-f8ef042f03d9', 'e2ff9d14-ccbf-5f36-bfd1-69ba1e83c1ce', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 10A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('ddd90701-7377-5f26-8046-1f1431e67e17', '7b73cd1a-106f-50fb-a656-135af105df01', 11, 'Level 11', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('6ed4809b-2b59-5c73-998e-89ce958db446', 'ddd90701-7377-5f26-8046-1f1431e67e17', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 11A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('8579909c-6e61-5995-87e9-51690643fb51', '7b73cd1a-106f-50fb-a656-135af105df01', 12, 'Level 12', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('23a4f86d-f036-5b6c-a50a-8a7bad1b486a', '8579909c-6e61-5995-87e9-51690643fb51', '7b73cd1a-106f-50fb-a656-135af105df01', 'Suite 12A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('3a2b1817-c00d-5a67-8f20-03c5e98f8472', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 1, 'Level 1', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('67f2f2a6-f9cd-5c47-bb19-6b172a772ff2', '3a2b1817-c00d-5a67-8f20-03c5e98f8472', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 1A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('b0eb9bdf-4f69-5f84-a8d1-506bc80d713d', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 2, 'Level 2', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('163e0901-a5bf-5bae-b6af-fa5bffe0c8f8', 'b0eb9bdf-4f69-5f84-a8d1-506bc80d713d', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 2A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('a565d251-f6c9-571a-b7e7-119cc5a94d7d', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 3, 'Level 3', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('68da57fa-d555-536d-a10e-b7b6cdb3bb36', 'a565d251-f6c9-571a-b7e7-119cc5a94d7d', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 3A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('727a79ae-ff8b-51b7-b1f0-a16faf98ae40', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 4, 'Level 4', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('acc97ff1-ef3a-5e12-a21f-f340bbb772a0', '727a79ae-ff8b-51b7-b1f0-a16faf98ae40', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 4A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('e8c78439-cd09-5df0-9751-3be49a87ba91', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 5, 'Level 5', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('3a8fcd51-de0e-575e-b8b6-14af515ea7e8', 'e8c78439-cd09-5df0-9751-3be49a87ba91', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 5A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('10c387ba-b5eb-548c-b2b9-11cb75eadb49', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 6, 'Level 6', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('08d9f0d6-491e-5566-b5ea-0353a63c3870', '10c387ba-b5eb-548c-b2b9-11cb75eadb49', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 6A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('60e32b26-19a5-50da-a21f-207f22834c36', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 7, 'Level 7', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('a19793f9-c2f7-5058-aa8d-99d5d2efd0de', '60e32b26-19a5-50da-a21f-207f22834c36', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 7A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('ce65147f-2db2-5bdc-8d57-5c2a9bee9484', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 8, 'Level 8', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('a8a93bac-2dcd-599f-aa12-b32649bb893b', 'ce65147f-2db2-5bdc-8d57-5c2a9bee9484', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 8A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('fb83a9b3-51b2-5340-95df-dd7f33fb01b7', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 9, 'Level 9', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('338c5272-b2a7-5d43-88ec-6ae324d8bded', 'fb83a9b3-51b2-5340-95df-dd7f33fb01b7', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 9A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('ad6d0e59-9c25-5d07-bdb2-3d1ad141d200', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 10, 'Level 10', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('e4162740-d324-5e23-b144-207ea4514068', 'ad6d0e59-9c25-5d07-bdb2-3d1ad141d200', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 10A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('2ca06b49-edf1-5201-8654-76fdb19fddf5', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 11, 'Level 11', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('96ab05b5-aa57-5b33-b305-e5f4748c3d45', '2ca06b49-edf1-5201-8654-76fdb19fddf5', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 11A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('a05b0992-aa15-5296-a9b4-14da62b8c9a4', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 12, 'Level 12', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('b3fd32fb-8180-5832-b38b-b25471f7ff82', 'a05b0992-aa15-5296-a9b4-14da62b8c9a4', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Suite 12A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('008dd635-8fb8-5342-8552-00cf8f99744e', '3e058306-2411-592d-b063-2fb6028e7816', 1, 'Level 1', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('408ed8ea-e40c-56a0-98a7-ff1c3edbed84', '008dd635-8fb8-5342-8552-00cf8f99744e', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 1A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('4fb0baeb-14a4-53bf-82a6-ce23a0532be8', '3e058306-2411-592d-b063-2fb6028e7816', 2, 'Level 2', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('c6e5edae-9252-5357-93c1-d7508058cbbd', '4fb0baeb-14a4-53bf-82a6-ce23a0532be8', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 2A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('b4508f89-f121-54c3-9d19-4c5d578142a8', '3e058306-2411-592d-b063-2fb6028e7816', 3, 'Level 3', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('b385d791-251d-58b8-8d76-eedf0d8670db', 'b4508f89-f121-54c3-9d19-4c5d578142a8', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 3A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('ef33bb82-e97e-5f71-b71d-46e52d1dbae0', '3e058306-2411-592d-b063-2fb6028e7816', 4, 'Level 4', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('1ce66eeb-60f7-59cd-a80f-d29d6d2ac96b', 'ef33bb82-e97e-5f71-b71d-46e52d1dbae0', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 4A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('267d0a61-1481-57a0-b211-7dbf5b9d7581', '3e058306-2411-592d-b063-2fb6028e7816', 5, 'Level 5', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('31fa2513-f0c7-5aed-860f-f7d2078edf59', '267d0a61-1481-57a0-b211-7dbf5b9d7581', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 5A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('26d9ef90-928f-5c85-922d-3b630184e84e', '3e058306-2411-592d-b063-2fb6028e7816', 6, 'Level 6', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('b4638435-03e8-555b-bcff-e8d4e8a8af77', '26d9ef90-928f-5c85-922d-3b630184e84e', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 6A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('6a583451-7617-5ec3-96af-118ad94c34c0', '3e058306-2411-592d-b063-2fb6028e7816', 7, 'Level 7', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('1f5dd212-b9f4-5445-9729-68a881c9e4f5', '6a583451-7617-5ec3-96af-118ad94c34c0', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 7A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('76cf272f-5deb-5cce-9a49-0caa5d3dee45', '3e058306-2411-592d-b063-2fb6028e7816', 8, 'Level 8', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('f21c2f78-bd2a-5a08-92cc-3ac845aa7fb9', '76cf272f-5deb-5cce-9a49-0caa5d3dee45', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 8A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('326b2cc7-16ec-5bdd-ad70-3ecc8549b9d7', '3e058306-2411-592d-b063-2fb6028e7816', 9, 'Level 9', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('8b15f243-f27a-54eb-803f-a6e705ea15cf', '326b2cc7-16ec-5bdd-ad70-3ecc8549b9d7', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 9A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('9bbfa1db-0eb8-57c6-aace-05ed519b467e', '3e058306-2411-592d-b063-2fb6028e7816', 10, 'Level 10', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('a2607455-45c8-53ed-91cf-b45429baaacf', '9bbfa1db-0eb8-57c6-aace-05ed519b467e', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 10A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('64eeff51-1ca2-5291-87ef-63e95941fdf3', '3e058306-2411-592d-b063-2fb6028e7816', 11, 'Level 11', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('8857ad1d-fc3b-5d43-9b98-ba5883be096c', '64eeff51-1ca2-5291-87ef-63e95941fdf3', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 11A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('90546d8c-8ca6-59b4-a9e2-c63374e96b46', '3e058306-2411-592d-b063-2fb6028e7816', 12, 'Level 12', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('14ebc7a3-82de-58cd-af29-8e0f81a8891c', '90546d8c-8ca6-59b4-a9e2-c63374e96b46', '3e058306-2411-592d-b063-2fb6028e7816', 'Suite 12A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('fa559fc4-8815-59b7-a4d3-bb0b8ee25200', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 1, 'Level 1', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('7dfe205c-e8e8-5989-bd3b-a670acf6354c', 'fa559fc4-8815-59b7-a4d3-bb0b8ee25200', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 1A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('13bdf787-94e6-5f74-a590-7c15e8e742e7', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 2, 'Level 2', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('cc35e35b-44c3-507a-aa4e-e1544ee5c80e', '13bdf787-94e6-5f74-a590-7c15e8e742e7', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 2A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('d8144875-fec0-573d-8cea-fb931e5ef5a7', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 3, 'Level 3', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('03e9ca5d-0f55-51cf-864a-a58782b921c5', 'd8144875-fec0-573d-8cea-fb931e5ef5a7', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 3A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('6a0f025a-f038-5a50-a55b-4e1a531a2abc', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 4, 'Level 4', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('c160558a-0d68-59a7-8b35-7917963c2d9c', '6a0f025a-f038-5a50-a55b-4e1a531a2abc', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 4A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('98e3fc51-b075-5515-bc40-8c5d94c609f1', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 5, 'Level 5', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('460033a2-65cd-5df5-bc07-bcb5c4d7f7b0', '98e3fc51-b075-5515-bc40-8c5d94c609f1', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 5A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('33d4c51d-2747-54ee-9c19-ad6ddfa467f9', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 6, 'Level 6', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('27c89422-12c8-5074-bd95-5abe4fe5f202', '33d4c51d-2747-54ee-9c19-ad6ddfa467f9', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 6A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('5afa4879-c676-59a7-b199-b6c17c83d095', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 7, 'Level 7', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('71db72ae-5beb-5123-bfd0-006a89923739', '5afa4879-c676-59a7-b199-b6c17c83d095', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 7A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('3d34e99c-c3d2-5a31-b94a-2e7b1e862ccb', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 8, 'Level 8', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('bfa3c5f8-0b2c-5404-8d19-0b76e6225b56', '3d34e99c-c3d2-5a31-b94a-2e7b1e862ccb', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 8A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('c4c03ef0-4760-56a3-b65f-61a9fbbedf18', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 9, 'Level 9', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('2497104f-72aa-54e6-b221-17812692037a', 'c4c03ef0-4760-56a3-b65f-61a9fbbedf18', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 9A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('0b262cb4-0b8c-5bb0-a89a-edaa7175c26e', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 10, 'Level 10', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('dce80004-4b88-544e-bbff-26443d47f46b', '0b262cb4-0b8c-5bb0-a89a-edaa7175c26e', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 10A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('2badfc0f-e0ab-5ad2-9851-38a81d9f9a82', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 11, 'Level 11', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('76726aa7-1de7-5e56-80b4-cafcd5870dac', '2badfc0f-e0ab-5ad2-9851-38a81d9f9a82', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 11A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('ede02810-6dff-5af4-812e-8f8db7a77638', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 12, 'Level 12', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('82d14e57-c977-5fa9-917c-a21dd03a9afa', 'ede02810-6dff-5af4-812e-8f8db7a77638', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'Suite 12A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('d50aad7a-834c-5e2f-b6fd-7fdd0738510c', 'f12e9629-95ed-5722-80cc-a1397f627850', 1, 'Level 1', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('96b90f0d-2ea8-5889-b01c-80bc96dfa588', 'd50aad7a-834c-5e2f-b6fd-7fdd0738510c', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 1A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('bfd6b581-b50f-5986-8112-5ac376d899e7', 'f12e9629-95ed-5722-80cc-a1397f627850', 2, 'Level 2', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('42f0169c-7bbd-5131-91ad-907715fcacb3', 'bfd6b581-b50f-5986-8112-5ac376d899e7', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 2A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('09eef60a-5f64-582b-9d74-9176ebe97adc', 'f12e9629-95ed-5722-80cc-a1397f627850', 3, 'Level 3', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('dfeca540-672e-50d2-b12d-f917141c8c42', '09eef60a-5f64-582b-9d74-9176ebe97adc', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 3A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('b57187a4-cbe8-5274-8cc1-fa12f45bbaae', 'f12e9629-95ed-5722-80cc-a1397f627850', 4, 'Level 4', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('19a43f73-6503-501c-b63c-e9c34eceac2a', 'b57187a4-cbe8-5274-8cc1-fa12f45bbaae', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 4A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('49ab9b7f-603c-54ad-b10b-848b2a421044', 'f12e9629-95ed-5722-80cc-a1397f627850', 5, 'Level 5', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('25c88c49-9a4a-5884-9148-700ef52e1a63', '49ab9b7f-603c-54ad-b10b-848b2a421044', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 5A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('5fd566e5-5509-5854-999a-39aee0078b9f', 'f12e9629-95ed-5722-80cc-a1397f627850', 6, 'Level 6', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('823c225a-ceb5-50b1-8144-babcf533aa95', '5fd566e5-5509-5854-999a-39aee0078b9f', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 6A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('e6a9a3c6-3e6b-59df-b951-066247bd2f94', 'f12e9629-95ed-5722-80cc-a1397f627850', 7, 'Level 7', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('4a11c100-5cb7-5f4a-b9c7-5fdda813dfcc', 'e6a9a3c6-3e6b-59df-b951-066247bd2f94', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 7A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('30a4a543-471e-57aa-9809-b30027437463', 'f12e9629-95ed-5722-80cc-a1397f627850', 8, 'Level 8', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('82e5254c-a9f6-5320-8e4f-2675eaeaf01e', '30a4a543-471e-57aa-9809-b30027437463', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 8A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('8d3e3b2a-8ab8-522f-a2d6-45ac11abe443', 'f12e9629-95ed-5722-80cc-a1397f627850', 9, 'Level 9', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('74993990-370a-5444-9021-f811358c78fb', '8d3e3b2a-8ab8-522f-a2d6-45ac11abe443', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 9A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('547021ba-4d46-5f60-9565-49b596fbdb36', 'f12e9629-95ed-5722-80cc-a1397f627850', 10, 'Level 10', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('766db387-8409-5cd1-897c-ba3d51486f1d', '547021ba-4d46-5f60-9565-49b596fbdb36', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 10A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('84a219f3-0c7e-5989-ba4c-d70689386601', 'f12e9629-95ed-5722-80cc-a1397f627850', 11, 'Level 11', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('dd9e5390-fac9-513e-8081-5057c85f6504', '84a219f3-0c7e-5989-ba4c-d70689386601', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 11A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('99ae03c7-b512-5299-9450-c57e54d85d65', 'f12e9629-95ed-5722-80cc-a1397f627850', 12, 'Level 12', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('8b295b30-e480-5eb8-9b52-f24e0b9b8748', '99ae03c7-b512-5299-9450-c57e54d85d65', 'f12e9629-95ed-5722-80cc-a1397f627850', 'Suite 12A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('2eb5a229-4c31-5bd9-8119-e490fd3b6bdd', '8f4a8708-a87c-5887-99b5-865c3a98b511', 1, 'Level 1', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('4f135aa7-b7d0-5cb0-a914-5e97a1c7a499', '2eb5a229-4c31-5bd9-8119-e490fd3b6bdd', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 1A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('625e5498-2758-5b22-9f9e-e246440f774e', '8f4a8708-a87c-5887-99b5-865c3a98b511', 2, 'Level 2', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('bcf51a23-a923-51b9-85b3-c13df570ea0a', '625e5498-2758-5b22-9f9e-e246440f774e', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 2A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('abbe9380-f13b-5342-8704-a3af79304894', '8f4a8708-a87c-5887-99b5-865c3a98b511', 3, 'Level 3', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('688e05ab-2327-50ee-a049-d0ff0213cadc', 'abbe9380-f13b-5342-8704-a3af79304894', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 3A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('605f3e99-e280-5562-b495-c3099df2e538', '8f4a8708-a87c-5887-99b5-865c3a98b511', 4, 'Level 4', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('81a12a2f-f034-59f5-a995-dd90a160ed47', '605f3e99-e280-5562-b495-c3099df2e538', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 4A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('007b5712-c710-5060-b7fa-781eecf4b592', '8f4a8708-a87c-5887-99b5-865c3a98b511', 5, 'Level 5', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('ec081090-ad8f-52a4-95a8-3e9f30a78b31', '007b5712-c710-5060-b7fa-781eecf4b592', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 5A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('14a31299-d4b4-5681-b5dd-20af1ee68f01', '8f4a8708-a87c-5887-99b5-865c3a98b511', 6, 'Level 6', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('c33a3055-6f8f-570c-bff4-b516e288039e', '14a31299-d4b4-5681-b5dd-20af1ee68f01', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 6A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('8df92d3b-0277-5524-9821-ed6d79d5d5d2', '8f4a8708-a87c-5887-99b5-865c3a98b511', 7, 'Level 7', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('1fe89f6d-b30f-55fc-b82d-6451ff876c68', '8df92d3b-0277-5524-9821-ed6d79d5d5d2', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 7A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('8a64ddde-fc63-543d-a1a5-4c7a3bc356e6', '8f4a8708-a87c-5887-99b5-865c3a98b511', 8, 'Level 8', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('ac419eda-afb6-5ae3-ad08-23770e15fc4c', '8a64ddde-fc63-543d-a1a5-4c7a3bc356e6', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 8A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('9ea25bcd-3937-5e74-8d0c-952559cc6efe', '8f4a8708-a87c-5887-99b5-865c3a98b511', 9, 'Level 9', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('b3a576c6-3cf1-5139-91ae-871255fb8967', '9ea25bcd-3937-5e74-8d0c-952559cc6efe', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 9A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('e0fbbd8c-a453-5872-83f8-4ac1d4b1026b', '8f4a8708-a87c-5887-99b5-865c3a98b511', 10, 'Level 10', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('4125d13d-1603-529b-9405-255a86dabe54', 'e0fbbd8c-a453-5872-83f8-4ac1d4b1026b', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 10A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('a27409f6-a7b7-55f2-9126-b493d8a82e4b', '8f4a8708-a87c-5887-99b5-865c3a98b511', 11, 'Level 11', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('15979393-0f3d-5ce6-8abb-ad4c31fa1487', 'a27409f6-a7b7-55f2-9126-b493d8a82e4b', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 11A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('164bbb6d-e803-5f37-b9f1-b7f8c2728f82', '8f4a8708-a87c-5887-99b5-865c3a98b511', 12, 'Level 12', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('403cb2a4-72f0-5414-b426-014687511680', '164bbb6d-e803-5f37-b9f1-b7f8c2728f82', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Suite 12A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('e347a284-670e-593e-a34e-f3cc53909f58', '954c3214-8467-528f-95c6-a320de9f579d', 1, 'Level 1', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('58f96067-9bd0-5e67-b304-0c8983598465', 'e347a284-670e-593e-a34e-f3cc53909f58', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 1A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('97c8a62a-1d50-55d3-9792-c51c74beb401', '954c3214-8467-528f-95c6-a320de9f579d', 2, 'Level 2', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('624f5b52-eddc-5a6d-8d87-dcdd7d6c6738', '97c8a62a-1d50-55d3-9792-c51c74beb401', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 2A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('4eb2a214-d84b-51da-b8bf-38d2e768d66c', '954c3214-8467-528f-95c6-a320de9f579d', 3, 'Level 3', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('cc313ffe-0a25-5162-9885-dde2d6efe5e1', '4eb2a214-d84b-51da-b8bf-38d2e768d66c', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 3A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('56e95352-175f-5bb8-8be6-3d44642c4967', '954c3214-8467-528f-95c6-a320de9f579d', 4, 'Level 4', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('158d86a7-a778-54d9-85b6-fcf26033888f', '56e95352-175f-5bb8-8be6-3d44642c4967', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 4A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('dea3ff66-717f-5584-86ec-001ada85ae7f', '954c3214-8467-528f-95c6-a320de9f579d', 5, 'Level 5', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('993d5332-4c53-5882-abb8-120e3e283170', 'dea3ff66-717f-5584-86ec-001ada85ae7f', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 5A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('6ff77949-9a82-53cf-8b92-5034c7802934', '954c3214-8467-528f-95c6-a320de9f579d', 6, 'Level 6', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('41f67e5a-3758-570f-82c8-0986d45860ba', '6ff77949-9a82-53cf-8b92-5034c7802934', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 6A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('31efa58c-8901-5de3-bb59-93f5d42d1eee', '954c3214-8467-528f-95c6-a320de9f579d', 7, 'Level 7', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('afd14363-96b5-5323-bf50-17aebf1ae572', '31efa58c-8901-5de3-bb59-93f5d42d1eee', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 7A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('5c861560-9644-532a-a22e-e57f66d5ccf3', '954c3214-8467-528f-95c6-a320de9f579d', 8, 'Level 8', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('224d8528-7c8b-5e93-bc75-7424bf57df19', '5c861560-9644-532a-a22e-e57f66d5ccf3', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 8A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('6a1ad8a5-f9c4-5f3b-a1b6-3ea36cb1d77b', '954c3214-8467-528f-95c6-a320de9f579d', 9, 'Level 9', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('b8b737f2-fc89-5e86-a43d-9a06c6a19ea7', '6a1ad8a5-f9c4-5f3b-a1b6-3ea36cb1d77b', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 9A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('4b0d54cb-fac9-5a15-9b91-c329dcb53cd2', '954c3214-8467-528f-95c6-a320de9f579d', 10, 'Level 10', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('35c054a3-80df-5b57-a0b3-b0e3cf8836c0', '4b0d54cb-fac9-5a15-9b91-c329dcb53cd2', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 10A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('e0eef283-c522-5e10-8389-ca5b2f18ceb7', '954c3214-8467-528f-95c6-a320de9f579d', 11, 'Level 11', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('3add85bd-fcca-5957-bb37-11ab9efd2f0a', 'e0eef283-c522-5e10-8389-ca5b2f18ceb7', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 11A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('167fcf94-c06c-530a-8a53-c65616674e65', '954c3214-8467-528f-95c6-a320de9f579d', 12, 'Level 12', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('d013fd20-33d8-5078-be33-4504e4f64a47', '167fcf94-c06c-530a-8a53-c65616674e65', '954c3214-8467-528f-95c6-a320de9f579d', 'Suite 12A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('1086591a-7c74-5fad-8e1a-ba7fcd1626c8', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 1, 'Level 1', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('6f35e8d6-a1f1-5f7e-9c0d-6ba81747c27f', '1086591a-7c74-5fad-8e1a-ba7fcd1626c8', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 1A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('e294d81f-1082-5ab9-ba74-761340c69a71', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 2, 'Level 2', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('ce420dfd-5c81-59a8-854c-3797e542a166', 'e294d81f-1082-5ab9-ba74-761340c69a71', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 2A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('51b063b0-b7cd-5238-b664-5dad1a3993c5', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 3, 'Level 3', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('b4ee7030-5de5-5e8b-9ead-63c1d36b0861', '51b063b0-b7cd-5238-b664-5dad1a3993c5', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 3A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('bc4097cb-c535-5f9d-bf06-479b45f1895c', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 4, 'Level 4', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('81f9e2bb-cb5a-5084-85af-880377778e1c', 'bc4097cb-c535-5f9d-bf06-479b45f1895c', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 4A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('1ae6a26c-8b8c-53db-8f0c-2e754817a70b', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 5, 'Level 5', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('c71872f1-acda-5443-a16b-0d2c8533cf89', '1ae6a26c-8b8c-53db-8f0c-2e754817a70b', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 5A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('8c1e6078-4fbf-5a07-b9fb-22583b4d7185', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 6, 'Level 6', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('511b123b-1eed-5525-87e2-420dabe563fc', '8c1e6078-4fbf-5a07-b9fb-22583b4d7185', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 6A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('5d66ff9e-cc90-5082-a741-cd158567379f', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 7, 'Level 7', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('b9b70063-e7ca-52ee-a2c2-ba6a71e67f09', '5d66ff9e-cc90-5082-a741-cd158567379f', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 7A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('19e517b6-2a25-59ce-a2fe-383712437468', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 8, 'Level 8', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('a3c91ebd-7657-5cf3-8327-92306b79efa5', '19e517b6-2a25-59ce-a2fe-383712437468', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 8A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('bf2d7ce3-42f1-57fd-b196-08aada2ae943', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 9, 'Level 9', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('d065c3c3-9913-51f1-aecb-eb25718d6c3d', 'bf2d7ce3-42f1-57fd-b196-08aada2ae943', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 9A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('452e99bc-d949-51c0-ad20-af5a3a30acd5', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 10, 'Level 10', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('8197a826-117c-5fa6-8f62-738afbc36be5', '452e99bc-d949-51c0-ad20-af5a3a30acd5', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 10A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('c27415d3-58d9-5b22-b26f-b39ee581ca0f', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 11, 'Level 11', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('51331822-0b1f-54de-850a-bdc259e14048', 'c27415d3-58d9-5b22-b26f-b39ee581ca0f', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 11A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;
INSERT INTO floors (floor_id, building_id, level, name, gross_area_sqft) VALUES ('9619ac9f-ecbb-5104-a724-c242074eb064', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 12, 'Level 12', 12000) ON CONFLICT (floor_id) DO NOTHING;
INSERT INTO spaces (space_id, floor_id, building_id, name, space_type, gross_area_sqft) VALUES ('9052cd28-de77-5f8c-a65e-df0a7f6d8019', '9619ac9f-ecbb-5104-a724-c242074eb064', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'Suite 12A', 'office', 7000) ON CONFLICT (space_id) DO NOTHING;

-- ── assets, the equipment on them, and the meters that read them ──
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('dd0ea919-0496-5588-ad92-63cb5a427333', '00000000-0000-0000-0000-000000000001', 'B-001-AHU-01', 'Air handling unit 1', '4791b70b-862a-5a75-8d24-5fe13d039131') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('ced12824-6004-53f8-9d0a-03c4440843ab', 'dd0ea919-0496-5588-ad92-63cb5a427333', 'Air handling unit 1 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('eebf27f9-1747-523a-893e-ab48998b0e4a', '00000000-0000-0000-0000-000000000001', 'B-001-CHILLER-02', 'Chiller 2', '4791b70b-862a-5a75-8d24-5fe13d039131') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('f70bd672-48b4-5b74-8345-293a58053599', 'eebf27f9-1747-523a-893e-ab48998b0e4a', 'Chiller 2 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('36a73028-2dc1-51c8-bdb4-bca2c93cb4bd', '4791b70b-862a-5a75-8d24-5fe13d039131', 'eebf27f9-1747-523a-893e-ab48998b0e4a', 'MPAN-B-001-2', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('e11e58f5-f16d-5ecb-a5e0-3a6d992b3b28', '00000000-0000-0000-0000-000000000001', 'B-001-BOILER-03', 'Boiler 3', '4791b70b-862a-5a75-8d24-5fe13d039131') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('db1b149a-e649-58c4-9d88-2e11ed431776', 'e11e58f5-f16d-5ecb-a5e0-3a6d992b3b28', 'Boiler 3 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('629eeb0b-5252-57ae-9927-5d336137dd12', '4791b70b-862a-5a75-8d24-5fe13d039131', 'e11e58f5-f16d-5ecb-a5e0-3a6d992b3b28', 'MPAN-B-001-3', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('e4409ada-71c8-55f8-9d39-bf4f256c1afb', '00000000-0000-0000-0000-000000000001', 'B-001-LIFT-04', 'Passenger lift 4', '4791b70b-862a-5a75-8d24-5fe13d039131') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('b64fd8d3-33e8-55e3-b4d0-cdd4c821f687', 'e4409ada-71c8-55f8-9d39-bf4f256c1afb', 'Passenger lift 4 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('f56f18be-d3a7-541d-8533-da610df59679', '00000000-0000-0000-0000-000000000001', 'B-001-FIRE-PANEL-05', 'Fire alarm panel 5', '4791b70b-862a-5a75-8d24-5fe13d039131') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('cb31b6ec-25f6-576e-bdf1-58802fe6bedb', 'f56f18be-d3a7-541d-8533-da610df59679', 'Fire alarm panel 5 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('03e80d4f-a07e-58c9-861b-c26df72ec4bf', '00000000-0000-0000-0000-000000000001', 'B-001-PUMP-06', 'Booster pump 6', '4791b70b-862a-5a75-8d24-5fe13d039131') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('abc5f94b-df99-5115-af3c-46d386de6498', '03e80d4f-a07e-58c9-861b-c26df72ec4bf', 'Booster pump 6 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('429004fa-fc06-5b80-94d5-8e9c2a8f3b10', '00000000-0000-0000-0000-000000000001', 'B-002-AHU-01', 'Air handling unit 1', '7b73cd1a-106f-50fb-a656-135af105df01') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('f7baadfd-ff0c-5ad5-9dfb-b7751ee34679', '429004fa-fc06-5b80-94d5-8e9c2a8f3b10', 'Air handling unit 1 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('664965c1-1fb0-5336-9ffd-48de970577d1', '00000000-0000-0000-0000-000000000001', 'B-002-CHILLER-02', 'Chiller 2', '7b73cd1a-106f-50fb-a656-135af105df01') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('c8e0653a-9c78-5bbf-9bec-dceceb8acd82', '664965c1-1fb0-5336-9ffd-48de970577d1', 'Chiller 2 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('2b70c312-faf5-598b-8ba6-e3c721af26c9', '7b73cd1a-106f-50fb-a656-135af105df01', '664965c1-1fb0-5336-9ffd-48de970577d1', 'MPAN-B-002-2', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('0acf24d4-ceee-5ced-ae56-b63692e16847', '00000000-0000-0000-0000-000000000001', 'B-002-BOILER-03', 'Boiler 3', '7b73cd1a-106f-50fb-a656-135af105df01') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('02781a7e-a169-5dba-bca2-42e36117d1eb', '0acf24d4-ceee-5ced-ae56-b63692e16847', 'Boiler 3 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('8000d680-ed20-5e2b-8881-0922a0c1e049', '7b73cd1a-106f-50fb-a656-135af105df01', '0acf24d4-ceee-5ced-ae56-b63692e16847', 'MPAN-B-002-3', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('2e5272c1-b631-5014-8aa3-f9eea9210c77', '00000000-0000-0000-0000-000000000001', 'B-002-LIFT-04', 'Passenger lift 4', '7b73cd1a-106f-50fb-a656-135af105df01') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('600d0ce0-c171-597a-9ada-3c23dd7f2045', '2e5272c1-b631-5014-8aa3-f9eea9210c77', 'Passenger lift 4 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('da307d4c-d51d-57e2-a4b8-dd756929e2f1', '00000000-0000-0000-0000-000000000001', 'B-002-FIRE-PANEL-05', 'Fire alarm panel 5', '7b73cd1a-106f-50fb-a656-135af105df01') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('88e41b3e-9cf4-58cd-b86a-7e0070763171', 'da307d4c-d51d-57e2-a4b8-dd756929e2f1', 'Fire alarm panel 5 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('3def3c6a-2be5-56a6-8325-62f4a85dbe9d', '00000000-0000-0000-0000-000000000001', 'B-002-PUMP-06', 'Booster pump 6', '7b73cd1a-106f-50fb-a656-135af105df01') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('f17d53ba-37e2-56cc-aec0-291cb1f860ac', '3def3c6a-2be5-56a6-8325-62f4a85dbe9d', 'Booster pump 6 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('b30fb9fa-24d6-5d73-a5be-6da750144269', '00000000-0000-0000-0000-000000000001', 'B-003-AHU-01', 'Air handling unit 1', '425f5590-404f-5d25-ac68-cac5c9cdebcc') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('752cb3ff-5c19-5191-87bc-48b349d373a7', 'b30fb9fa-24d6-5d73-a5be-6da750144269', 'Air handling unit 1 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('7d331fff-510d-50b8-973e-692eae8ea799', '00000000-0000-0000-0000-000000000001', 'B-003-CHILLER-02', 'Chiller 2', '425f5590-404f-5d25-ac68-cac5c9cdebcc') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('f751ab09-5eba-5c5f-aef4-02bc1dc8159c', '7d331fff-510d-50b8-973e-692eae8ea799', 'Chiller 2 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('073eff85-9c66-5305-83be-5a07a1afaa84', '425f5590-404f-5d25-ac68-cac5c9cdebcc', '7d331fff-510d-50b8-973e-692eae8ea799', 'MPAN-B-003-2', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('0334e71e-d572-5ae5-a151-b67f1871ffa5', '00000000-0000-0000-0000-000000000001', 'B-003-BOILER-03', 'Boiler 3', '425f5590-404f-5d25-ac68-cac5c9cdebcc') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('65c99afb-160f-50f4-be8f-6c9435b4dc37', '0334e71e-d572-5ae5-a151-b67f1871ffa5', 'Boiler 3 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('98e659a7-2045-516d-9830-c4b191394b72', '425f5590-404f-5d25-ac68-cac5c9cdebcc', '0334e71e-d572-5ae5-a151-b67f1871ffa5', 'MPAN-B-003-3', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('ea4d27f1-90c0-5dbc-a730-342b25170d90', '00000000-0000-0000-0000-000000000001', 'B-003-LIFT-04', 'Passenger lift 4', '425f5590-404f-5d25-ac68-cac5c9cdebcc') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('cededd83-0d62-5322-8481-6371a5fb3077', 'ea4d27f1-90c0-5dbc-a730-342b25170d90', 'Passenger lift 4 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('50e234c8-cb33-5d0a-91b5-d71bb929ce07', '00000000-0000-0000-0000-000000000001', 'B-003-FIRE-PANEL-05', 'Fire alarm panel 5', '425f5590-404f-5d25-ac68-cac5c9cdebcc') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('3241efce-be0d-5251-a43c-a7f7df33caed', '50e234c8-cb33-5d0a-91b5-d71bb929ce07', 'Fire alarm panel 5 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('db725fd8-7f67-5e03-9d6d-a8aa794163b8', '00000000-0000-0000-0000-000000000001', 'B-003-PUMP-06', 'Booster pump 6', '425f5590-404f-5d25-ac68-cac5c9cdebcc') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('8813a555-b775-5002-8677-0a542cfa2ea7', 'db725fd8-7f67-5e03-9d6d-a8aa794163b8', 'Booster pump 6 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('76799320-0025-5689-b7af-dbd30acbefa4', '00000000-0000-0000-0000-000000000001', 'B-004-AHU-01', 'Air handling unit 1', '3e058306-2411-592d-b063-2fb6028e7816') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('a75765b6-01f4-568d-beaf-6f3ddb48dfee', '76799320-0025-5689-b7af-dbd30acbefa4', 'Air handling unit 1 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('280fe01a-63a5-5709-8384-04ddc05277d5', '00000000-0000-0000-0000-000000000001', 'B-004-CHILLER-02', 'Chiller 2', '3e058306-2411-592d-b063-2fb6028e7816') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('9070ea8d-4e8c-56d2-9531-60f8bfc8c821', '280fe01a-63a5-5709-8384-04ddc05277d5', 'Chiller 2 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('2736b9c1-c6d1-546b-a046-ceebaf09366d', '3e058306-2411-592d-b063-2fb6028e7816', '280fe01a-63a5-5709-8384-04ddc05277d5', 'MPAN-B-004-2', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('29d55d39-3500-5789-9cef-2566806824d4', '00000000-0000-0000-0000-000000000001', 'B-004-BOILER-03', 'Boiler 3', '3e058306-2411-592d-b063-2fb6028e7816') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('eab51559-c912-5171-a22e-293ac25e89e5', '29d55d39-3500-5789-9cef-2566806824d4', 'Boiler 3 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('335eeb59-514e-5d43-b743-90f4cf8e088d', '3e058306-2411-592d-b063-2fb6028e7816', '29d55d39-3500-5789-9cef-2566806824d4', 'MPAN-B-004-3', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('592307d7-3ff4-5ed5-bdcb-87be84270adb', '00000000-0000-0000-0000-000000000001', 'B-004-LIFT-04', 'Passenger lift 4', '3e058306-2411-592d-b063-2fb6028e7816') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('7302ed7b-a570-537b-bb94-7ec9a48f2fa7', '592307d7-3ff4-5ed5-bdcb-87be84270adb', 'Passenger lift 4 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('4206efda-79b1-5299-be32-8b1ca78f7b62', '00000000-0000-0000-0000-000000000001', 'B-004-FIRE-PANEL-05', 'Fire alarm panel 5', '3e058306-2411-592d-b063-2fb6028e7816') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('1be5acc3-6e8e-51dc-9e87-dfdb1dbe8b8b', '4206efda-79b1-5299-be32-8b1ca78f7b62', 'Fire alarm panel 5 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('433951a3-da2d-53bc-8f2a-191084a33dae', '00000000-0000-0000-0000-000000000001', 'B-004-PUMP-06', 'Booster pump 6', '3e058306-2411-592d-b063-2fb6028e7816') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('238c8658-c5bf-5cb2-a6dc-8ba2d3485230', '433951a3-da2d-53bc-8f2a-191084a33dae', 'Booster pump 6 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('b2cf711e-f6bd-52d2-b4e1-2af43ee43978', '00000000-0000-0000-0000-000000000001', 'B-005-AHU-01', 'Air handling unit 1', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('ce43b52f-44f6-5273-bf4a-80e4ee9c71af', 'b2cf711e-f6bd-52d2-b4e1-2af43ee43978', 'Air handling unit 1 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('976e8937-a231-5e52-8fd3-a0aac7739d09', '00000000-0000-0000-0000-000000000001', 'B-005-CHILLER-02', 'Chiller 2', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('f3222acd-7d6d-53bb-9a4c-89f552ea46d7', '976e8937-a231-5e52-8fd3-a0aac7739d09', 'Chiller 2 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('997b2a39-a593-55d0-bd63-ec2cb1d714b5', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '976e8937-a231-5e52-8fd3-a0aac7739d09', 'MPAN-B-005-2', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('857dde5a-26d8-528e-afd4-b7a95a8cf0e6', '00000000-0000-0000-0000-000000000001', 'B-005-BOILER-03', 'Boiler 3', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('04cddd93-9830-5210-abf4-c26eaf46ddae', '857dde5a-26d8-528e-afd4-b7a95a8cf0e6', 'Boiler 3 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('7bb9e58e-cf99-5d92-88d7-83558bfffac6', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '857dde5a-26d8-528e-afd4-b7a95a8cf0e6', 'MPAN-B-005-3', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('3508d97e-0620-5d55-9cf7-1abe9df5c137', '00000000-0000-0000-0000-000000000001', 'B-005-LIFT-04', 'Passenger lift 4', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('50edbdd8-7db2-5002-9354-6569bad28bc7', '3508d97e-0620-5d55-9cf7-1abe9df5c137', 'Passenger lift 4 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('7eeb405b-6b38-5c0b-8d03-6716dc1b0437', '00000000-0000-0000-0000-000000000001', 'B-005-FIRE-PANEL-05', 'Fire alarm panel 5', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('4b835867-b383-5a66-aeba-70587c7514f7', '7eeb405b-6b38-5c0b-8d03-6716dc1b0437', 'Fire alarm panel 5 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('e4e5dcca-f809-568a-a485-703c99344d46', '00000000-0000-0000-0000-000000000001', 'B-005-PUMP-06', 'Booster pump 6', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('bdbb5834-c95b-5d39-96af-c8cdd6e6d83d', 'e4e5dcca-f809-568a-a485-703c99344d46', 'Booster pump 6 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('353d9b19-8c15-56ba-bca5-d9f9e569d78d', '00000000-0000-0000-0000-000000000001', 'B-006-AHU-01', 'Air handling unit 1', 'f12e9629-95ed-5722-80cc-a1397f627850') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('da939b0e-e667-592d-8044-50f21b266e79', '353d9b19-8c15-56ba-bca5-d9f9e569d78d', 'Air handling unit 1 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('51ca2bbb-9314-5e34-9fef-b912c3cf5c80', '00000000-0000-0000-0000-000000000001', 'B-006-CHILLER-02', 'Chiller 2', 'f12e9629-95ed-5722-80cc-a1397f627850') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('1a3c9677-16aa-5e45-9cb8-977a5a36c54b', '51ca2bbb-9314-5e34-9fef-b912c3cf5c80', 'Chiller 2 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('e17f0ac8-cb13-59f0-8f9f-ba03d6f2eee9', 'f12e9629-95ed-5722-80cc-a1397f627850', '51ca2bbb-9314-5e34-9fef-b912c3cf5c80', 'MPAN-B-006-2', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('0d3695e1-33a6-5216-9165-85f40c67ac4d', '00000000-0000-0000-0000-000000000001', 'B-006-BOILER-03', 'Boiler 3', 'f12e9629-95ed-5722-80cc-a1397f627850') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('f70ce570-a1b4-56c4-a103-b8be3be85e8f', '0d3695e1-33a6-5216-9165-85f40c67ac4d', 'Boiler 3 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('732ecf5c-d421-51c6-9f39-81d972f04c2c', 'f12e9629-95ed-5722-80cc-a1397f627850', '0d3695e1-33a6-5216-9165-85f40c67ac4d', 'MPAN-B-006-3', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('b1fdfa3d-ccee-53ef-93a2-d9222156268d', '00000000-0000-0000-0000-000000000001', 'B-006-LIFT-04', 'Passenger lift 4', 'f12e9629-95ed-5722-80cc-a1397f627850') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('d58059e1-cb64-5ef5-9d04-dd0f61b39002', 'b1fdfa3d-ccee-53ef-93a2-d9222156268d', 'Passenger lift 4 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('cc040444-df01-55d3-b5fd-b3b030e88f7a', '00000000-0000-0000-0000-000000000001', 'B-006-FIRE-PANEL-05', 'Fire alarm panel 5', 'f12e9629-95ed-5722-80cc-a1397f627850') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('15ce5b4a-9cec-5e29-a9e3-4df3d1823096', 'cc040444-df01-55d3-b5fd-b3b030e88f7a', 'Fire alarm panel 5 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('0c40ec2a-b658-5209-b742-d399bf5bb9ab', '00000000-0000-0000-0000-000000000001', 'B-006-PUMP-06', 'Booster pump 6', 'f12e9629-95ed-5722-80cc-a1397f627850') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('c2aadd63-270f-5fd9-9e1f-5fa3dce2c33b', '0c40ec2a-b658-5209-b742-d399bf5bb9ab', 'Booster pump 6 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('3de2cd64-627d-5475-8d06-a2f607c9b926', '00000000-0000-0000-0000-000000000001', 'B-007-AHU-01', 'Air handling unit 1', '8f4a8708-a87c-5887-99b5-865c3a98b511') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('d34e5503-3660-54df-899d-4ee306a780b0', '3de2cd64-627d-5475-8d06-a2f607c9b926', 'Air handling unit 1 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('2a9af40b-69fd-5dd8-a78a-8d74bc221b99', '00000000-0000-0000-0000-000000000001', 'B-007-CHILLER-02', 'Chiller 2', '8f4a8708-a87c-5887-99b5-865c3a98b511') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('b28af3bf-118a-5d60-971e-84382c2f4098', '2a9af40b-69fd-5dd8-a78a-8d74bc221b99', 'Chiller 2 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('c41d1a31-b0b0-571c-b2e8-0a0b9637ec7f', '8f4a8708-a87c-5887-99b5-865c3a98b511', '2a9af40b-69fd-5dd8-a78a-8d74bc221b99', 'MPAN-B-007-2', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('1dd7a700-7be1-5d81-a6fc-721aacb9d8ce', '00000000-0000-0000-0000-000000000001', 'B-007-BOILER-03', 'Boiler 3', '8f4a8708-a87c-5887-99b5-865c3a98b511') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('7758d2c8-5214-5ff5-a3fd-96392f2a24d4', '1dd7a700-7be1-5d81-a6fc-721aacb9d8ce', 'Boiler 3 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('c5cc6c02-6a00-5354-ac08-68f77eb16652', '8f4a8708-a87c-5887-99b5-865c3a98b511', '1dd7a700-7be1-5d81-a6fc-721aacb9d8ce', 'MPAN-B-007-3', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('fca4a921-1aee-5976-a18e-4c1507444f1c', '00000000-0000-0000-0000-000000000001', 'B-007-LIFT-04', 'Passenger lift 4', '8f4a8708-a87c-5887-99b5-865c3a98b511') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('63eaaabe-b268-59df-93e6-72e13b262586', 'fca4a921-1aee-5976-a18e-4c1507444f1c', 'Passenger lift 4 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('c0a35667-e86a-5a58-9c7b-9b544d65db1e', '00000000-0000-0000-0000-000000000001', 'B-007-FIRE-PANEL-05', 'Fire alarm panel 5', '8f4a8708-a87c-5887-99b5-865c3a98b511') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('1dcb30b5-3fc8-5e9d-bd13-0d4542117187', 'c0a35667-e86a-5a58-9c7b-9b544d65db1e', 'Fire alarm panel 5 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('8e76074e-97ae-5a6c-82d1-eff46008ac6f', '00000000-0000-0000-0000-000000000001', 'B-007-PUMP-06', 'Booster pump 6', '8f4a8708-a87c-5887-99b5-865c3a98b511') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('2b126cc1-06bf-58b2-8ac1-638cc4db23e8', '8e76074e-97ae-5a6c-82d1-eff46008ac6f', 'Booster pump 6 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('344378fa-067d-54ea-aa6a-b600b8b3f8a1', '00000000-0000-0000-0000-000000000001', 'B-008-AHU-01', 'Air handling unit 1', '954c3214-8467-528f-95c6-a320de9f579d') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('da4b521f-2e36-501d-b435-84b47134a133', '344378fa-067d-54ea-aa6a-b600b8b3f8a1', 'Air handling unit 1 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('a7cb5b5c-4bd3-5606-948a-12b9d3ed1ba8', '00000000-0000-0000-0000-000000000001', 'B-008-CHILLER-02', 'Chiller 2', '954c3214-8467-528f-95c6-a320de9f579d') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('e6982d4a-343b-50dc-854f-83fe0332665f', 'a7cb5b5c-4bd3-5606-948a-12b9d3ed1ba8', 'Chiller 2 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('57eed6e7-9f7b-5bdb-8829-06afe4efe8d1', '954c3214-8467-528f-95c6-a320de9f579d', 'a7cb5b5c-4bd3-5606-948a-12b9d3ed1ba8', 'MPAN-B-008-2', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('920546f6-1ef8-592a-be76-08f5ca4aadff', '00000000-0000-0000-0000-000000000001', 'B-008-BOILER-03', 'Boiler 3', '954c3214-8467-528f-95c6-a320de9f579d') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('74884774-2e95-516c-9a85-959876c654c1', '920546f6-1ef8-592a-be76-08f5ca4aadff', 'Boiler 3 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('a2f13535-00a3-536c-b79a-59d031591411', '954c3214-8467-528f-95c6-a320de9f579d', '920546f6-1ef8-592a-be76-08f5ca4aadff', 'MPAN-B-008-3', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('19ce5615-d72a-5826-adf6-ee7cb7a0f8fa', '00000000-0000-0000-0000-000000000001', 'B-008-LIFT-04', 'Passenger lift 4', '954c3214-8467-528f-95c6-a320de9f579d') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('b5df0a1b-36f9-5aa2-a7d0-4c407460444e', '19ce5615-d72a-5826-adf6-ee7cb7a0f8fa', 'Passenger lift 4 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('fe38e102-f2b1-5cca-a875-8f21727260a0', '00000000-0000-0000-0000-000000000001', 'B-008-FIRE-PANEL-05', 'Fire alarm panel 5', '954c3214-8467-528f-95c6-a320de9f579d') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('fdbaedcb-6b7d-55c8-912a-66b513947a34', 'fe38e102-f2b1-5cca-a875-8f21727260a0', 'Fire alarm panel 5 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('35787d0f-79c4-5d29-9770-da42776fdd8b', '00000000-0000-0000-0000-000000000001', 'B-008-PUMP-06', 'Booster pump 6', '954c3214-8467-528f-95c6-a320de9f579d') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('0e7a01ac-a590-517a-902c-fd517e5079d1', '35787d0f-79c4-5d29-9770-da42776fdd8b', 'Booster pump 6 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('7b83b3e6-b2f4-5c0c-8cf3-889a967df989', '00000000-0000-0000-0000-000000000001', 'B-009-AHU-01', 'Air handling unit 1', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('6ef75fca-d5a8-5c36-88aa-4413411cd563', '7b83b3e6-b2f4-5c0c-8cf3-889a967df989', 'Air handling unit 1 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('b8c96a26-fdd4-5129-a080-5cb3d6403539', '00000000-0000-0000-0000-000000000001', 'B-009-CHILLER-02', 'Chiller 2', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('2a8fc012-b995-54be-8482-e3146b02774d', 'b8c96a26-fdd4-5129-a080-5cb3d6403539', 'Chiller 2 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('e86c6060-7a7e-5ad5-933b-b30bcddd32f8', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'b8c96a26-fdd4-5129-a080-5cb3d6403539', 'MPAN-B-009-2', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('44a5acb4-c8bb-58e0-a149-a26d7a9e03f2', '00000000-0000-0000-0000-000000000001', 'B-009-BOILER-03', 'Boiler 3', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('887a84c9-37de-5e7f-938c-4df0ce0b5033', '44a5acb4-c8bb-58e0-a149-a26d7a9e03f2', 'Boiler 3 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO meters (meter_id, building_id, asset_id, mpan_mprn, meter_type, unit) VALUES ('81bba2b3-2f84-59d4-988f-91ca85416ae5', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', '44a5acb4-c8bb-58e0-a149-a26d7a9e03f2', 'MPAN-B-009-3', 'electricity', 'kWh') ON CONFLICT (meter_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('c366e172-736c-5720-9638-af41b87c3372', '00000000-0000-0000-0000-000000000001', 'B-009-LIFT-04', 'Passenger lift 4', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('c13b3fdc-2495-53e6-97e9-dd2304d331a8', 'c366e172-736c-5720-9638-af41b87c3372', 'Passenger lift 4 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('2cea3f88-c394-510e-bc50-11d5569a370f', '00000000-0000-0000-0000-000000000001', 'B-009-FIRE-PANEL-05', 'Fire alarm panel 5', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('48b0d822-6e6f-5d53-a84c-192c22bfe08b', '2cea3f88-c394-510e-bc50-11d5569a370f', 'Fire alarm panel 5 - main unit') ON CONFLICT (equipment_id) DO NOTHING;
INSERT INTO assets (id, organization_id, asset_code, asset_name, building_id) VALUES ('c4cba9c3-f619-55b2-a937-e1641fe3323d', '00000000-0000-0000-0000-000000000001', 'B-009-PUMP-06', 'Booster pump 6', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10') ON CONFLICT (id) DO NOTHING;
INSERT INTO equipment (equipment_id, asset_id, name) VALUES ('4a25fdd7-ff87-5a49-98f8-39740ecb9988', 'c4cba9c3-f619-55b2-a937-e1641fe3323d', 'Booster pump 6 - main unit') ON CONFLICT (equipment_id) DO NOTHING;

-- ── vendors, and the contracts they hold ──
INSERT INTO vendors (id, organization_id, vendor_name, vendor_code, country, status) VALUES ('91bba149-25cb-546b-b349-b14a2fab86c7', '00000000-0000-0000-0000-000000000001', 'Apex Lift Services', 'APEX', 'UK', 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('86811d64-3657-5226-9d46-6e63f080f7fd', '00000000-0000-0000-0000-000000000001', '91bba149-25cb-546b-b349-b14a2fab86c7', 'Lifts maintenance framework', '2024-11-27', '2027-11-12', 240000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('44080219-47a8-5d12-9f52-b773ec94f323', '00000000-0000-0000-0000-000000000001', '91bba149-25cb-546b-b349-b14a2fab86c7', 'Lifts PPM — Bishopsgate Tower', '2025-07-05', '2026-06-30', 48000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendors (id, organization_id, vendor_name, vendor_code, country, status) VALUES ('3cf6cc98-9f75-580f-88e5-27c29631945f', '00000000-0000-0000-0000-000000000001', 'Clearwater Hygiene', 'CLRW', 'UK', 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('b4df37a8-6414-5cbd-a45d-26d807fae530', '00000000-0000-0000-0000-000000000001', '3cf6cc98-9f75-580f-88e5-27c29631945f', 'Water maintenance framework', '2024-11-27', '2027-11-12', 240000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('7d7bc3f8-7dbc-5dc7-ab89-6fd346cf095c', '00000000-0000-0000-0000-000000000001', '3cf6cc98-9f75-580f-88e5-27c29631945f', 'Water PPM — Kingsway House', '2025-07-05', '2026-06-30', 48000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendors (id, organization_id, vendor_name, vendor_code, country, status) VALUES ('aa4f1e43-501c-56b8-8d13-6cac6902d6c2', '00000000-0000-0000-0000-000000000001', 'Gulf Mechanical', 'GULF', 'AE', 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('ff908327-d623-58fe-8880-58289748f902', '00000000-0000-0000-0000-000000000001', 'aa4f1e43-501c-56b8-8d13-6cac6902d6c2', 'Mechanical maintenance framework', '2024-11-27', '2027-11-12', 240000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('2556bf7e-a76d-55a2-818c-6936048b9e09', '00000000-0000-0000-0000-000000000001', 'aa4f1e43-501c-56b8-8d13-6cac6902d6c2', 'Mechanical PPM — Town Hall', '2025-07-05', '2026-06-30', 48000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendors (id, organization_id, vendor_name, vendor_code, country, status) VALUES ('f0d562e2-dab9-53a3-ad79-73a970da2417', '00000000-0000-0000-0000-000000000001', 'Northgate Electrical', 'NGEL', 'US', 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('d51c709b-052f-569a-8c2c-630dd25226c3', '00000000-0000-0000-0000-000000000001', 'f0d562e2-dab9-53a3-ad79-73a970da2417', 'Electrical maintenance framework', '2024-11-27', '2027-11-12', 240000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('46240e44-f257-5cae-bf45-c3a9d4a6c0c4', '00000000-0000-0000-0000-000000000001', 'f0d562e2-dab9-53a3-ad79-73a970da2417', 'Electrical PPM — Meridian Quay', '2025-07-05', '2026-06-30', 48000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendors (id, organization_id, vendor_name, vendor_code, country, status) VALUES ('4221732c-f44d-537c-8c95-76299a19dfef', '00000000-0000-0000-0000-000000000001', 'Sentinel Fire & Security', 'SENT', 'UK', 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('e0200dbc-c0e2-5b06-ba3b-34019f8ce1cf', '00000000-0000-0000-0000-000000000001', '4221732c-f44d-537c-8c95-76299a19dfef', 'Fire maintenance framework', '2024-11-27', '2027-11-12', 240000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('ab71c216-f832-531f-ae47-75d4599fcda5', '00000000-0000-0000-0000-000000000001', '4221732c-f44d-537c-8c95-76299a19dfef', 'Fire PPM — AN Other House', '2025-07-05', '2026-06-30', 48000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendors (id, organization_id, vendor_name, vendor_code, country, status) VALUES ('3f07cc45-fa35-52be-9c80-154df200ece9', '00000000-0000-0000-0000-000000000001', 'Halden Building Services', 'HALD', 'UK', 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('a35f4489-82ed-5b2e-bdd6-b58c52a1c4a7', '00000000-0000-0000-0000-000000000001', '3f07cc45-fa35-52be-9c80-154df200ece9', 'Mechanical maintenance framework', '2024-11-27', '2027-11-12', 240000, 'active') ON CONFLICT (id) DO NOTHING;
INSERT INTO vendor_contracts (id, organization_id, vendor_id, contract_name, contract_start, contract_end, contract_value, status) VALUES ('54fe48c7-7c1a-51f1-9db9-3b4877f6a3c9', '00000000-0000-0000-0000-000000000001', '3f07cc45-fa35-52be-9c80-154df200ece9', 'Mechanical PPM — Riverside Court', '2025-07-05', '2026-06-30', 48000, 'active') ON CONFLICT (id) DO NOTHING;

-- ── work orders: one closed, one open, per asset ──
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('1ee80a43-5d6e-51cc-b7dd-c599611f373c', '00000000-0000-0000-0000-000000000001', 'WO-B-001-11', 'Annual service - Air handling unit 1', 'closed', '4791b70b-862a-5a75-8d24-5fe13d039131', 'dd0ea919-0496-5588-ad92-63cb5a427333', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('fc1a11a7-617f-5061-9ecc-61747c01cd48', '00000000-0000-0000-0000-000000000001', 'WO-B-001-12', 'Fault call-out - Air handling unit 1', 'open', '4791b70b-862a-5a75-8d24-5fe13d039131', 'dd0ea919-0496-5588-ad92-63cb5a427333', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('c3384416-41cd-590a-b37e-2c0305fda27a', '00000000-0000-0000-0000-000000000001', 'WO-B-001-21', 'Annual service - Chiller 2', 'closed', '4791b70b-862a-5a75-8d24-5fe13d039131', 'eebf27f9-1747-523a-893e-ab48998b0e4a', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('24241e80-1dda-5635-8bbd-698cebf300ae', '00000000-0000-0000-0000-000000000001', 'WO-B-001-22', 'Fault call-out - Chiller 2', 'open', '4791b70b-862a-5a75-8d24-5fe13d039131', 'eebf27f9-1747-523a-893e-ab48998b0e4a', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('ccfad982-be92-5897-8d5d-7226761b8700', '00000000-0000-0000-0000-000000000001', 'WO-B-001-31', 'Annual service - Boiler 3', 'closed', '4791b70b-862a-5a75-8d24-5fe13d039131', 'e11e58f5-f16d-5ecb-a5e0-3a6d992b3b28', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('70064cb1-4cbe-560b-a433-224a950d5fa5', '00000000-0000-0000-0000-000000000001', 'WO-B-001-32', 'Fault call-out - Boiler 3', 'open', '4791b70b-862a-5a75-8d24-5fe13d039131', 'e11e58f5-f16d-5ecb-a5e0-3a6d992b3b28', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('9babebbe-562f-5c2d-a44c-dcc8a46a3cae', '00000000-0000-0000-0000-000000000001', 'WO-B-001-41', 'Annual service - Passenger lift 4', 'closed', '4791b70b-862a-5a75-8d24-5fe13d039131', 'e4409ada-71c8-55f8-9d39-bf4f256c1afb', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('c2e85a37-2db9-5cb4-87ab-6cffab11f6bd', '00000000-0000-0000-0000-000000000001', 'WO-B-001-42', 'Fault call-out - Passenger lift 4', 'open', '4791b70b-862a-5a75-8d24-5fe13d039131', 'e4409ada-71c8-55f8-9d39-bf4f256c1afb', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('89d3f6b0-0eb6-5727-afe9-289afcaf9a06', '00000000-0000-0000-0000-000000000001', 'WO-B-001-51', 'Annual service - Fire alarm panel 5', 'closed', '4791b70b-862a-5a75-8d24-5fe13d039131', 'f56f18be-d3a7-541d-8533-da610df59679', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('87844ca6-5be3-5615-9f23-ed04a4a63122', '00000000-0000-0000-0000-000000000001', 'WO-B-001-52', 'Fault call-out - Fire alarm panel 5', 'open', '4791b70b-862a-5a75-8d24-5fe13d039131', 'f56f18be-d3a7-541d-8533-da610df59679', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('2da99271-2a18-56db-a033-73584d8572a3', '00000000-0000-0000-0000-000000000001', 'WO-B-001-61', 'Annual service - Booster pump 6', 'closed', '4791b70b-862a-5a75-8d24-5fe13d039131', '03e80d4f-a07e-58c9-861b-c26df72ec4bf', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('f68a5a38-5483-5392-8d74-e1d6daf2bb13', '00000000-0000-0000-0000-000000000001', 'WO-B-001-62', 'Fault call-out - Booster pump 6', 'open', '4791b70b-862a-5a75-8d24-5fe13d039131', '03e80d4f-a07e-58c9-861b-c26df72ec4bf', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('8fe1ef59-7bf0-54b2-a697-7b72d8ba43fc', '00000000-0000-0000-0000-000000000001', 'WO-B-002-11', 'Annual service - Air handling unit 1', 'closed', '7b73cd1a-106f-50fb-a656-135af105df01', '429004fa-fc06-5b80-94d5-8e9c2a8f3b10', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('5f9b683e-9e60-5003-94c6-56cf28cdc539', '00000000-0000-0000-0000-000000000001', 'WO-B-002-12', 'Fault call-out - Air handling unit 1', 'open', '7b73cd1a-106f-50fb-a656-135af105df01', '429004fa-fc06-5b80-94d5-8e9c2a8f3b10', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('f81183d3-8e12-5c6c-9c01-2c421000aca3', '00000000-0000-0000-0000-000000000001', 'WO-B-002-21', 'Annual service - Chiller 2', 'closed', '7b73cd1a-106f-50fb-a656-135af105df01', '664965c1-1fb0-5336-9ffd-48de970577d1', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('f0b2e498-a9e2-545e-9354-d1745322d1de', '00000000-0000-0000-0000-000000000001', 'WO-B-002-22', 'Fault call-out - Chiller 2', 'open', '7b73cd1a-106f-50fb-a656-135af105df01', '664965c1-1fb0-5336-9ffd-48de970577d1', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('6c62bf2e-b4e6-50c4-ba15-059c14852fce', '00000000-0000-0000-0000-000000000001', 'WO-B-002-31', 'Annual service - Boiler 3', 'closed', '7b73cd1a-106f-50fb-a656-135af105df01', '0acf24d4-ceee-5ced-ae56-b63692e16847', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('504f3cd8-a199-5241-810b-0504b39d333c', '00000000-0000-0000-0000-000000000001', 'WO-B-002-32', 'Fault call-out - Boiler 3', 'open', '7b73cd1a-106f-50fb-a656-135af105df01', '0acf24d4-ceee-5ced-ae56-b63692e16847', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('714f55d5-a5c1-578e-bef9-62df11054149', '00000000-0000-0000-0000-000000000001', 'WO-B-002-41', 'Annual service - Passenger lift 4', 'closed', '7b73cd1a-106f-50fb-a656-135af105df01', '2e5272c1-b631-5014-8aa3-f9eea9210c77', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('e36a4ca9-d334-5a14-9082-2b23df7ef2ec', '00000000-0000-0000-0000-000000000001', 'WO-B-002-42', 'Fault call-out - Passenger lift 4', 'open', '7b73cd1a-106f-50fb-a656-135af105df01', '2e5272c1-b631-5014-8aa3-f9eea9210c77', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('73c86f8c-1028-5d56-8a5d-601ab76c9ad0', '00000000-0000-0000-0000-000000000001', 'WO-B-002-51', 'Annual service - Fire alarm panel 5', 'closed', '7b73cd1a-106f-50fb-a656-135af105df01', 'da307d4c-d51d-57e2-a4b8-dd756929e2f1', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('11b16e25-bd46-557c-ac9f-df7648a8cb5f', '00000000-0000-0000-0000-000000000001', 'WO-B-002-52', 'Fault call-out - Fire alarm panel 5', 'open', '7b73cd1a-106f-50fb-a656-135af105df01', 'da307d4c-d51d-57e2-a4b8-dd756929e2f1', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('84ab8e83-cc04-5a7b-a00e-676b4a67f4b6', '00000000-0000-0000-0000-000000000001', 'WO-B-002-61', 'Annual service - Booster pump 6', 'closed', '7b73cd1a-106f-50fb-a656-135af105df01', '3def3c6a-2be5-56a6-8325-62f4a85dbe9d', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('435da66d-b073-5be9-a853-f7a9dee212b6', '00000000-0000-0000-0000-000000000001', 'WO-B-002-62', 'Fault call-out - Booster pump 6', 'open', '7b73cd1a-106f-50fb-a656-135af105df01', '3def3c6a-2be5-56a6-8325-62f4a85dbe9d', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('60432544-190d-5c52-8e30-54925b437cc9', '00000000-0000-0000-0000-000000000001', 'WO-B-003-11', 'Annual service - Air handling unit 1', 'closed', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'b30fb9fa-24d6-5d73-a5be-6da750144269', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('ff00ee30-364b-5568-9970-ec54aeb5a838', '00000000-0000-0000-0000-000000000001', 'WO-B-003-12', 'Fault call-out - Air handling unit 1', 'open', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'b30fb9fa-24d6-5d73-a5be-6da750144269', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('289ac0d1-81c9-5f34-b41d-ede342603f9c', '00000000-0000-0000-0000-000000000001', 'WO-B-003-21', 'Annual service - Chiller 2', 'closed', '425f5590-404f-5d25-ac68-cac5c9cdebcc', '7d331fff-510d-50b8-973e-692eae8ea799', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('c19d7b2a-ab4c-5104-8fa6-ce093e6e7bc9', '00000000-0000-0000-0000-000000000001', 'WO-B-003-22', 'Fault call-out - Chiller 2', 'open', '425f5590-404f-5d25-ac68-cac5c9cdebcc', '7d331fff-510d-50b8-973e-692eae8ea799', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('51e1df78-06ed-57ab-8a40-2254a7b546cc', '00000000-0000-0000-0000-000000000001', 'WO-B-003-31', 'Annual service - Boiler 3', 'closed', '425f5590-404f-5d25-ac68-cac5c9cdebcc', '0334e71e-d572-5ae5-a151-b67f1871ffa5', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('433dd307-f8e8-5fd7-8b2b-4cd1880d4258', '00000000-0000-0000-0000-000000000001', 'WO-B-003-32', 'Fault call-out - Boiler 3', 'open', '425f5590-404f-5d25-ac68-cac5c9cdebcc', '0334e71e-d572-5ae5-a151-b67f1871ffa5', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('bd1ebfe5-d9f6-5211-8398-4c8b380b9fa7', '00000000-0000-0000-0000-000000000001', 'WO-B-003-41', 'Annual service - Passenger lift 4', 'closed', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'ea4d27f1-90c0-5dbc-a730-342b25170d90', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('3cd6a594-0531-548b-b5cf-662318ac2074', '00000000-0000-0000-0000-000000000001', 'WO-B-003-42', 'Fault call-out - Passenger lift 4', 'open', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'ea4d27f1-90c0-5dbc-a730-342b25170d90', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('c3294014-133c-569f-813c-3d8512a17574', '00000000-0000-0000-0000-000000000001', 'WO-B-003-51', 'Annual service - Fire alarm panel 5', 'closed', '425f5590-404f-5d25-ac68-cac5c9cdebcc', '50e234c8-cb33-5d0a-91b5-d71bb929ce07', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('647008d6-bb57-5486-88b2-1d1ba5b6733f', '00000000-0000-0000-0000-000000000001', 'WO-B-003-52', 'Fault call-out - Fire alarm panel 5', 'open', '425f5590-404f-5d25-ac68-cac5c9cdebcc', '50e234c8-cb33-5d0a-91b5-d71bb929ce07', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('e6b96965-9a95-57fc-8c4e-5e306c0042e1', '00000000-0000-0000-0000-000000000001', 'WO-B-003-61', 'Annual service - Booster pump 6', 'closed', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'db725fd8-7f67-5e03-9d6d-a8aa794163b8', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('5acc58f1-8d78-54c8-8deb-0afc3044365c', '00000000-0000-0000-0000-000000000001', 'WO-B-003-62', 'Fault call-out - Booster pump 6', 'open', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'db725fd8-7f67-5e03-9d6d-a8aa794163b8', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('6ae846b8-2428-5912-98ee-3c9922afc416', '00000000-0000-0000-0000-000000000001', 'WO-B-004-11', 'Annual service - Air handling unit 1', 'closed', '3e058306-2411-592d-b063-2fb6028e7816', '76799320-0025-5689-b7af-dbd30acbefa4', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('eb9a2c61-9fb7-531d-b513-395752530b31', '00000000-0000-0000-0000-000000000001', 'WO-B-004-12', 'Fault call-out - Air handling unit 1', 'open', '3e058306-2411-592d-b063-2fb6028e7816', '76799320-0025-5689-b7af-dbd30acbefa4', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('ea75b5dd-989c-5ed0-995c-2610624cee22', '00000000-0000-0000-0000-000000000001', 'WO-B-004-21', 'Annual service - Chiller 2', 'closed', '3e058306-2411-592d-b063-2fb6028e7816', '280fe01a-63a5-5709-8384-04ddc05277d5', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('cac0d046-5da2-5764-a2ce-65e4b9f2d135', '00000000-0000-0000-0000-000000000001', 'WO-B-004-22', 'Fault call-out - Chiller 2', 'open', '3e058306-2411-592d-b063-2fb6028e7816', '280fe01a-63a5-5709-8384-04ddc05277d5', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('79f498ba-6b41-5ca3-8152-bcdca4b1adcb', '00000000-0000-0000-0000-000000000001', 'WO-B-004-31', 'Annual service - Boiler 3', 'closed', '3e058306-2411-592d-b063-2fb6028e7816', '29d55d39-3500-5789-9cef-2566806824d4', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('c8187d8f-db43-5d0a-a59d-1643bae6d8a4', '00000000-0000-0000-0000-000000000001', 'WO-B-004-32', 'Fault call-out - Boiler 3', 'open', '3e058306-2411-592d-b063-2fb6028e7816', '29d55d39-3500-5789-9cef-2566806824d4', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('2ddae5fd-379a-55f2-9b07-001878586c97', '00000000-0000-0000-0000-000000000001', 'WO-B-004-41', 'Annual service - Passenger lift 4', 'closed', '3e058306-2411-592d-b063-2fb6028e7816', '592307d7-3ff4-5ed5-bdcb-87be84270adb', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('5f9de8e4-c640-5af9-b5b0-c9b8ba8eb532', '00000000-0000-0000-0000-000000000001', 'WO-B-004-42', 'Fault call-out - Passenger lift 4', 'open', '3e058306-2411-592d-b063-2fb6028e7816', '592307d7-3ff4-5ed5-bdcb-87be84270adb', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('15d85b36-d414-51cd-9318-69a8c58a4be7', '00000000-0000-0000-0000-000000000001', 'WO-B-004-51', 'Annual service - Fire alarm panel 5', 'closed', '3e058306-2411-592d-b063-2fb6028e7816', '4206efda-79b1-5299-be32-8b1ca78f7b62', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('1e5914cd-95db-529a-bb99-bbbb289a8720', '00000000-0000-0000-0000-000000000001', 'WO-B-004-52', 'Fault call-out - Fire alarm panel 5', 'open', '3e058306-2411-592d-b063-2fb6028e7816', '4206efda-79b1-5299-be32-8b1ca78f7b62', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('09beeb02-e826-503c-8f12-20eeb45f5981', '00000000-0000-0000-0000-000000000001', 'WO-B-004-61', 'Annual service - Booster pump 6', 'closed', '3e058306-2411-592d-b063-2fb6028e7816', '433951a3-da2d-53bc-8f2a-191084a33dae', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('1939d0a7-55f2-5c4b-9251-05907c2cc447', '00000000-0000-0000-0000-000000000001', 'WO-B-004-62', 'Fault call-out - Booster pump 6', 'open', '3e058306-2411-592d-b063-2fb6028e7816', '433951a3-da2d-53bc-8f2a-191084a33dae', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('6113c05a-7cf4-5c21-afb1-14cc14217c3b', '00000000-0000-0000-0000-000000000001', 'WO-B-005-11', 'Annual service - Air handling unit 1', 'closed', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'b2cf711e-f6bd-52d2-b4e1-2af43ee43978', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('30c048f4-6924-523b-a4c0-190fbf2039c0', '00000000-0000-0000-0000-000000000001', 'WO-B-005-12', 'Fault call-out - Air handling unit 1', 'open', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'b2cf711e-f6bd-52d2-b4e1-2af43ee43978', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('8439777d-5fd0-54c4-a06b-1663215ff439', '00000000-0000-0000-0000-000000000001', 'WO-B-005-21', 'Annual service - Chiller 2', 'closed', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '976e8937-a231-5e52-8fd3-a0aac7739d09', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('99902731-b9c7-53af-a462-c78ff9b32c4c', '00000000-0000-0000-0000-000000000001', 'WO-B-005-22', 'Fault call-out - Chiller 2', 'open', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '976e8937-a231-5e52-8fd3-a0aac7739d09', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('5f681a42-d814-577d-a421-6718b6891a9a', '00000000-0000-0000-0000-000000000001', 'WO-B-005-31', 'Annual service - Boiler 3', 'closed', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '857dde5a-26d8-528e-afd4-b7a95a8cf0e6', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('41f46dc7-b92f-5246-9793-463d89064a51', '00000000-0000-0000-0000-000000000001', 'WO-B-005-32', 'Fault call-out - Boiler 3', 'open', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '857dde5a-26d8-528e-afd4-b7a95a8cf0e6', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('98edf046-54af-57b8-87a9-979b1c9fabb8', '00000000-0000-0000-0000-000000000001', 'WO-B-005-41', 'Annual service - Passenger lift 4', 'closed', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '3508d97e-0620-5d55-9cf7-1abe9df5c137', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('b29b1a29-ff0b-562f-9186-827bde8d69a3', '00000000-0000-0000-0000-000000000001', 'WO-B-005-42', 'Fault call-out - Passenger lift 4', 'open', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '3508d97e-0620-5d55-9cf7-1abe9df5c137', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('00ba98b8-1aae-5933-96f1-b40eb2848ce8', '00000000-0000-0000-0000-000000000001', 'WO-B-005-51', 'Annual service - Fire alarm panel 5', 'closed', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '7eeb405b-6b38-5c0b-8d03-6716dc1b0437', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('e98ae9ad-bef8-57e5-b5fc-dac947a304ac', '00000000-0000-0000-0000-000000000001', 'WO-B-005-52', 'Fault call-out - Fire alarm panel 5', 'open', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', '7eeb405b-6b38-5c0b-8d03-6716dc1b0437', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('46ac7913-cc52-5686-ba38-c29db045361d', '00000000-0000-0000-0000-000000000001', 'WO-B-005-61', 'Annual service - Booster pump 6', 'closed', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'e4e5dcca-f809-568a-a485-703c99344d46', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('601a3b53-345e-51a5-802e-05c70e629be2', '00000000-0000-0000-0000-000000000001', 'WO-B-005-62', 'Fault call-out - Booster pump 6', 'open', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'e4e5dcca-f809-568a-a485-703c99344d46', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('99530a38-d569-5680-89ff-3803dd41e6f8', '00000000-0000-0000-0000-000000000001', 'WO-B-006-11', 'Annual service - Air handling unit 1', 'closed', 'f12e9629-95ed-5722-80cc-a1397f627850', '353d9b19-8c15-56ba-bca5-d9f9e569d78d', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('788bccd6-7fff-5e50-b48e-8b5b580f6ff6', '00000000-0000-0000-0000-000000000001', 'WO-B-006-12', 'Fault call-out - Air handling unit 1', 'open', 'f12e9629-95ed-5722-80cc-a1397f627850', '353d9b19-8c15-56ba-bca5-d9f9e569d78d', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('e0c0b762-f8de-5d9d-a623-d178b765a46e', '00000000-0000-0000-0000-000000000001', 'WO-B-006-21', 'Annual service - Chiller 2', 'closed', 'f12e9629-95ed-5722-80cc-a1397f627850', '51ca2bbb-9314-5e34-9fef-b912c3cf5c80', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('7257ba0b-bbf3-5376-9999-23a505ecc8b4', '00000000-0000-0000-0000-000000000001', 'WO-B-006-22', 'Fault call-out - Chiller 2', 'open', 'f12e9629-95ed-5722-80cc-a1397f627850', '51ca2bbb-9314-5e34-9fef-b912c3cf5c80', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('329c441e-8b9f-5e2f-b93f-f03cc3dbb35b', '00000000-0000-0000-0000-000000000001', 'WO-B-006-31', 'Annual service - Boiler 3', 'closed', 'f12e9629-95ed-5722-80cc-a1397f627850', '0d3695e1-33a6-5216-9165-85f40c67ac4d', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('d281abfa-3575-559f-bb80-80bcf0c851be', '00000000-0000-0000-0000-000000000001', 'WO-B-006-32', 'Fault call-out - Boiler 3', 'open', 'f12e9629-95ed-5722-80cc-a1397f627850', '0d3695e1-33a6-5216-9165-85f40c67ac4d', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('2d11a68e-b049-5d73-85e0-b52f9182d997', '00000000-0000-0000-0000-000000000001', 'WO-B-006-41', 'Annual service - Passenger lift 4', 'closed', 'f12e9629-95ed-5722-80cc-a1397f627850', 'b1fdfa3d-ccee-53ef-93a2-d9222156268d', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('55257665-bb1f-555d-8bc6-4466fd98de6c', '00000000-0000-0000-0000-000000000001', 'WO-B-006-42', 'Fault call-out - Passenger lift 4', 'open', 'f12e9629-95ed-5722-80cc-a1397f627850', 'b1fdfa3d-ccee-53ef-93a2-d9222156268d', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('583b857f-2a05-501c-9e6a-1b322761d12c', '00000000-0000-0000-0000-000000000001', 'WO-B-006-51', 'Annual service - Fire alarm panel 5', 'closed', 'f12e9629-95ed-5722-80cc-a1397f627850', 'cc040444-df01-55d3-b5fd-b3b030e88f7a', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('98585ebc-fc3e-51e0-be85-45d63e3bf5ef', '00000000-0000-0000-0000-000000000001', 'WO-B-006-52', 'Fault call-out - Fire alarm panel 5', 'open', 'f12e9629-95ed-5722-80cc-a1397f627850', 'cc040444-df01-55d3-b5fd-b3b030e88f7a', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('7b4bb135-d7fc-5861-9997-0599517852ff', '00000000-0000-0000-0000-000000000001', 'WO-B-006-61', 'Annual service - Booster pump 6', 'closed', 'f12e9629-95ed-5722-80cc-a1397f627850', '0c40ec2a-b658-5209-b742-d399bf5bb9ab', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('80fe941d-8462-5d48-a019-8331aab5b212', '00000000-0000-0000-0000-000000000001', 'WO-B-006-62', 'Fault call-out - Booster pump 6', 'open', 'f12e9629-95ed-5722-80cc-a1397f627850', '0c40ec2a-b658-5209-b742-d399bf5bb9ab', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('97d3ad62-f806-55e4-a3a1-ea5bb56c3375', '00000000-0000-0000-0000-000000000001', 'WO-B-007-11', 'Annual service - Air handling unit 1', 'closed', '8f4a8708-a87c-5887-99b5-865c3a98b511', '3de2cd64-627d-5475-8d06-a2f607c9b926', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('05c3fde3-ce8a-5b8b-bf74-19e1b1571bc3', '00000000-0000-0000-0000-000000000001', 'WO-B-007-12', 'Fault call-out - Air handling unit 1', 'open', '8f4a8708-a87c-5887-99b5-865c3a98b511', '3de2cd64-627d-5475-8d06-a2f607c9b926', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('1afa8e45-750a-571d-9af2-0050f5916196', '00000000-0000-0000-0000-000000000001', 'WO-B-007-21', 'Annual service - Chiller 2', 'closed', '8f4a8708-a87c-5887-99b5-865c3a98b511', '2a9af40b-69fd-5dd8-a78a-8d74bc221b99', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('3f048e72-acb7-5127-9591-438af81adad1', '00000000-0000-0000-0000-000000000001', 'WO-B-007-22', 'Fault call-out - Chiller 2', 'open', '8f4a8708-a87c-5887-99b5-865c3a98b511', '2a9af40b-69fd-5dd8-a78a-8d74bc221b99', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('df9a879f-e668-54ca-b627-ec07c8ff63ce', '00000000-0000-0000-0000-000000000001', 'WO-B-007-31', 'Annual service - Boiler 3', 'closed', '8f4a8708-a87c-5887-99b5-865c3a98b511', '1dd7a700-7be1-5d81-a6fc-721aacb9d8ce', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('fa6a9f25-cf87-5346-83e8-3065914eb9a7', '00000000-0000-0000-0000-000000000001', 'WO-B-007-32', 'Fault call-out - Boiler 3', 'open', '8f4a8708-a87c-5887-99b5-865c3a98b511', '1dd7a700-7be1-5d81-a6fc-721aacb9d8ce', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('fcaffeac-9115-527a-b16b-b832f433c081', '00000000-0000-0000-0000-000000000001', 'WO-B-007-41', 'Annual service - Passenger lift 4', 'closed', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'fca4a921-1aee-5976-a18e-4c1507444f1c', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('ff5adffb-fdd5-5526-b2cc-8ae0ff5bbc6d', '00000000-0000-0000-0000-000000000001', 'WO-B-007-42', 'Fault call-out - Passenger lift 4', 'open', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'fca4a921-1aee-5976-a18e-4c1507444f1c', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('fb90cd44-f551-5883-84b7-69c9eb934ce0', '00000000-0000-0000-0000-000000000001', 'WO-B-007-51', 'Annual service - Fire alarm panel 5', 'closed', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'c0a35667-e86a-5a58-9c7b-9b544d65db1e', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('b7cec91e-1626-59fa-bded-1bffd3ea46d9', '00000000-0000-0000-0000-000000000001', 'WO-B-007-52', 'Fault call-out - Fire alarm panel 5', 'open', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'c0a35667-e86a-5a58-9c7b-9b544d65db1e', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('01e9618d-6d2b-5384-9283-cbcf9dd60f5e', '00000000-0000-0000-0000-000000000001', 'WO-B-007-61', 'Annual service - Booster pump 6', 'closed', '8f4a8708-a87c-5887-99b5-865c3a98b511', '8e76074e-97ae-5a6c-82d1-eff46008ac6f', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('b4082724-3eaa-562d-8a77-a9934c286d49', '00000000-0000-0000-0000-000000000001', 'WO-B-007-62', 'Fault call-out - Booster pump 6', 'open', '8f4a8708-a87c-5887-99b5-865c3a98b511', '8e76074e-97ae-5a6c-82d1-eff46008ac6f', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('eb5b29ed-cb04-5614-b168-84e05e9f918c', '00000000-0000-0000-0000-000000000001', 'WO-B-008-11', 'Annual service - Air handling unit 1', 'closed', '954c3214-8467-528f-95c6-a320de9f579d', '344378fa-067d-54ea-aa6a-b600b8b3f8a1', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('40927b69-fecc-5805-98c3-fcc4f380144b', '00000000-0000-0000-0000-000000000001', 'WO-B-008-12', 'Fault call-out - Air handling unit 1', 'open', '954c3214-8467-528f-95c6-a320de9f579d', '344378fa-067d-54ea-aa6a-b600b8b3f8a1', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('5dbb03c1-9c0e-584f-9229-ef6d6ab806cc', '00000000-0000-0000-0000-000000000001', 'WO-B-008-21', 'Annual service - Chiller 2', 'closed', '954c3214-8467-528f-95c6-a320de9f579d', 'a7cb5b5c-4bd3-5606-948a-12b9d3ed1ba8', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('87cc31f0-f946-5b48-ba01-9ec44143746a', '00000000-0000-0000-0000-000000000001', 'WO-B-008-22', 'Fault call-out - Chiller 2', 'open', '954c3214-8467-528f-95c6-a320de9f579d', 'a7cb5b5c-4bd3-5606-948a-12b9d3ed1ba8', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('9dbea1d2-6b96-590e-bb9c-7b2811085b7b', '00000000-0000-0000-0000-000000000001', 'WO-B-008-31', 'Annual service - Boiler 3', 'closed', '954c3214-8467-528f-95c6-a320de9f579d', '920546f6-1ef8-592a-be76-08f5ca4aadff', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('b0e55ad9-27ff-50fe-b836-29109ad89058', '00000000-0000-0000-0000-000000000001', 'WO-B-008-32', 'Fault call-out - Boiler 3', 'open', '954c3214-8467-528f-95c6-a320de9f579d', '920546f6-1ef8-592a-be76-08f5ca4aadff', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('deb2198c-fa66-5cd6-a2a4-9222dd6d92f7', '00000000-0000-0000-0000-000000000001', 'WO-B-008-41', 'Annual service - Passenger lift 4', 'closed', '954c3214-8467-528f-95c6-a320de9f579d', '19ce5615-d72a-5826-adf6-ee7cb7a0f8fa', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('2815d158-89bd-5e3d-9dcf-0a6d26b801ea', '00000000-0000-0000-0000-000000000001', 'WO-B-008-42', 'Fault call-out - Passenger lift 4', 'open', '954c3214-8467-528f-95c6-a320de9f579d', '19ce5615-d72a-5826-adf6-ee7cb7a0f8fa', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('0fac0931-d959-5e0b-a4dd-a4697842ead5', '00000000-0000-0000-0000-000000000001', 'WO-B-008-51', 'Annual service - Fire alarm panel 5', 'closed', '954c3214-8467-528f-95c6-a320de9f579d', 'fe38e102-f2b1-5cca-a875-8f21727260a0', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('0f3d5feb-6598-550a-b6f7-2eddc66aaea2', '00000000-0000-0000-0000-000000000001', 'WO-B-008-52', 'Fault call-out - Fire alarm panel 5', 'open', '954c3214-8467-528f-95c6-a320de9f579d', 'fe38e102-f2b1-5cca-a875-8f21727260a0', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('af9b7c3e-ad5c-51ac-a9d4-0933abba11bc', '00000000-0000-0000-0000-000000000001', 'WO-B-008-61', 'Annual service - Booster pump 6', 'closed', '954c3214-8467-528f-95c6-a320de9f579d', '35787d0f-79c4-5d29-9770-da42776fdd8b', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('6289f95f-4cc1-5a89-8026-e47ce5c275de', '00000000-0000-0000-0000-000000000001', 'WO-B-008-62', 'Fault call-out - Booster pump 6', 'open', '954c3214-8467-528f-95c6-a320de9f579d', '35787d0f-79c4-5d29-9770-da42776fdd8b', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('dfd7620a-d696-509c-a8d8-f3c7f1462b08', '00000000-0000-0000-0000-000000000001', 'WO-B-009-11', 'Annual service - Air handling unit 1', 'closed', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', '7b83b3e6-b2f4-5c0c-8cf3-889a967df989', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('37cbe898-97cc-52c7-a628-9bfc4ea0ce8f', '00000000-0000-0000-0000-000000000001', 'WO-B-009-12', 'Fault call-out - Air handling unit 1', 'open', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', '7b83b3e6-b2f4-5c0c-8cf3-889a967df989', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('f2406095-46cd-5571-92e6-11715ffb296d', '00000000-0000-0000-0000-000000000001', 'WO-B-009-21', 'Annual service - Chiller 2', 'closed', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'b8c96a26-fdd4-5129-a080-5cb3d6403539', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('f725624e-88ca-541a-9d93-c9e3444007f3', '00000000-0000-0000-0000-000000000001', 'WO-B-009-22', 'Fault call-out - Chiller 2', 'open', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'b8c96a26-fdd4-5129-a080-5cb3d6403539', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('bd953dd9-e52a-5408-b25e-0609b24f3440', '00000000-0000-0000-0000-000000000001', 'WO-B-009-31', 'Annual service - Boiler 3', 'closed', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', '44a5acb4-c8bb-58e0-a149-a26d7a9e03f2', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('d79f35ed-d6af-5c76-933a-1692a39f20da', '00000000-0000-0000-0000-000000000001', 'WO-B-009-32', 'Fault call-out - Boiler 3', 'open', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', '44a5acb4-c8bb-58e0-a149-a26d7a9e03f2', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('20c76675-30e5-534e-aa82-e7ac011984e5', '00000000-0000-0000-0000-000000000001', 'WO-B-009-41', 'Annual service - Passenger lift 4', 'closed', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'c366e172-736c-5720-9638-af41b87c3372', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('8687d33a-995d-5fa2-8925-a3f47256f1a5', '00000000-0000-0000-0000-000000000001', 'WO-B-009-42', 'Fault call-out - Passenger lift 4', 'open', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'c366e172-736c-5720-9638-af41b87c3372', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('6eadb935-9b4d-5009-9e1f-c6ced9ee0386', '00000000-0000-0000-0000-000000000001', 'WO-B-009-51', 'Annual service - Fire alarm panel 5', 'closed', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', '2cea3f88-c394-510e-bc50-11d5569a370f', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('f91714c9-0a15-58aa-a21a-95b53d54ed0f', '00000000-0000-0000-0000-000000000001', 'WO-B-009-52', 'Fault call-out - Fire alarm panel 5', 'open', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', '2cea3f88-c394-510e-bc50-11d5569a370f', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('41a270f4-aa21-5800-b794-4028dc27a368', '00000000-0000-0000-0000-000000000001', 'WO-B-009-61', 'Annual service - Booster pump 6', 'closed', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'c4cba9c3-f619-55b2-a937-e1641fe3323d', '2025-09-03'::timestamptz, false) ON CONFLICT (id) DO NOTHING;
INSERT INTO work_orders (id, organization_id, wo_code, title, status, building_id, asset_id, raised_at, conflict_flag) VALUES ('a351197c-0302-5804-8297-9df11d1f48cd', '00000000-0000-0000-0000-000000000001', 'WO-B-009-62', 'Fault call-out - Booster pump 6', 'open', '264f4a18-f7b9-5ca0-baa5-9189ca55ef10', 'c4cba9c3-f619-55b2-a937-e1641fe3323d', '2025-12-23'::timestamptz, false) ON CONFLICT (id) DO NOTHING;

-- ── compliance certificates ──
-- Building scope. Each building gets a different slice, so portfolio coverage varies
-- instead of every building reporting the same percentage.
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('d13f2866-9d1e-5a7d-a9d7-bde0c00a1e0f', '4791b70b-862a-5a75-8d24-5fe13d039131', 'compliance_certificate', 'FRA', 'FRA-B-001-000.pdf', '2025-11-02'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('bc2a3e69-53ed-5e03-b7f1-d2fce07e58ea', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FRA', 'FRA', 'FRA-B-001-000', 'FRA-B-001-000',
        'Building', 'UK', '2025-11-02', '2026-11-02', '2026-11-02',
        'Current', 305, false, '4791b70b-862a-5a75-8d24-5fe13d039131', 'Bishopsgate Tower', 'B-001', 'B-001',
        NULL, 'A. Surveyor', 'REG-B-001-000', 'd13f2866-9d1e-5a7d-a9d7-bde0c00a1e0f', 'd13f2866-9d1e-5a7d-a9d7-bde0c00a1e0f',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('70534a8c-32f7-5c7e-91e6-259163199bf3', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EICR', 'EICR', 'EICR-B-001-001', 'EICR-B-001-001',
        'Building', 'UK', '2024-11-27', '2029-11-21', '2029-11-21',
        'Current', 1420, false, '4791b70b-862a-5a75-8d24-5fe13d039131', 'Bishopsgate Tower', 'B-001', 'B-001',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('b60c41e3-e8a5-5c7d-a045-3fc31a75ad27', '4791b70b-862a-5a75-8d24-5fe13d039131', 'compliance_certificate', 'CP17', 'CP17-B-001-002.pdf', '2025-03-07'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('c36df705-4183-586a-88f9-910b4afa5b66', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'CP17', 'CP17', 'CP17-B-001-002', 'CP17-B-001-002',
        'Building', 'UK', '2025-03-07', '2026-03-07', '2026-03-07',
        'Due for Renewal', 65, false, '4791b70b-862a-5a75-8d24-5fe13d039131', 'Bishopsgate Tower', 'B-001', 'B-001',
        NULL, 'A. Surveyor', 'REG-B-001-002', 'b60c41e3-e8a5-5c7d-a045-3fc31a75ad27', 'b60c41e3-e8a5-5c7d-a045-3fc31a75ad27',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('3612ba1b-770b-59f9-baf7-85fbb89da91c', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LOLER', 'LOLER', 'LOLER-B-001-003', 'LOLER-B-001-003',
        'Building', 'UK', '2025-07-15', '2026-01-13', '2026-01-13',
        'Expiring Soon', 12, false, '4791b70b-862a-5a75-8d24-5fe13d039131', 'Bishopsgate Tower', 'B-001', 'B-001',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('e05c069f-96b4-5ef5-991f-dca5518e98f3', '4791b70b-862a-5a75-8d24-5fe13d039131', 'compliance_certificate', 'EPC', 'EPC-B-001-004.pdf', '2022-03-03'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('ee3c19ac-d87f-5d6f-9e75-85b92b460561', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EPC', 'EPC', 'EPC-B-001-004', 'EPC-B-001-004',
        'Building', 'UK', '2022-03-03', '2029-06-04', '2029-06-04',
        'Current', 1250, false, '4791b70b-862a-5a75-8d24-5fe13d039131', 'Bishopsgate Tower', 'B-001', 'B-001',
        NULL, 'A. Surveyor', 'REG-B-001-004', 'e05c069f-96b4-5ef5-991f-dca5518e98f3', 'e05c069f-96b4-5ef5-991f-dca5518e98f3',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('0c429ae2-f935-55f1-a89a-23c92db5613f', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY-B-001-005', 'ASBESTOS_SURVEY-B-001-005',
        'Building', 'UK', '2024-11-27', '2025-11-27', '2025-11-27',
        'Lapsed', -35, true, '4791b70b-862a-5a75-8d24-5fe13d039131', 'Bishopsgate Tower', 'B-001', 'B-001',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('de8549a9-538a-5782-8b78-ea4eb9c2a970', '7b73cd1a-106f-50fb-a656-135af105df01', 'compliance_certificate', 'FRA', 'FRA-B-002-000.pdf', '2025-11-02'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('74627172-f3ac-5319-918e-4d99c573df17', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FRA', 'FRA', 'FRA-B-002-000', 'FRA-B-002-000',
        'Building', 'UK', '2025-11-02', '2026-11-02', '2026-11-02',
        'Current', 305, false, '7b73cd1a-106f-50fb-a656-135af105df01', 'Kingsway House', 'B-002', 'B-002',
        NULL, 'A. Surveyor', 'REG-B-002-000', 'de8549a9-538a-5782-8b78-ea4eb9c2a970', 'de8549a9-538a-5782-8b78-ea4eb9c2a970',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('b8e7b676-360a-5468-9904-5b7ff33ef4a7', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EICR', 'EICR', 'EICR-B-002-001', 'EICR-B-002-001',
        'Building', 'UK', '2024-11-27', '2029-11-21', '2029-11-21',
        'Current', 1420, false, '7b73cd1a-106f-50fb-a656-135af105df01', 'Kingsway House', 'B-002', 'B-002',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('f3c9ec21-09d6-54eb-8365-59911f58830a', '7b73cd1a-106f-50fb-a656-135af105df01', 'compliance_certificate', 'CP17', 'CP17-B-002-002.pdf', '2025-03-07'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('e45973e8-7803-5e52-82cb-f23a938f3c81', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'CP17', 'CP17', 'CP17-B-002-002', 'CP17-B-002-002',
        'Building', 'UK', '2025-03-07', '2026-03-07', '2026-03-07',
        'Due for Renewal', 65, false, '7b73cd1a-106f-50fb-a656-135af105df01', 'Kingsway House', 'B-002', 'B-002',
        NULL, 'A. Surveyor', 'REG-B-002-002', 'f3c9ec21-09d6-54eb-8365-59911f58830a', 'f3c9ec21-09d6-54eb-8365-59911f58830a',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('ceee068e-6ed8-5b6b-a0d1-2af1e23cfcba', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LOLER', 'LOLER', 'LOLER-B-002-003', 'LOLER-B-002-003',
        'Building', 'UK', '2025-07-15', '2026-01-13', '2026-01-13',
        'Expiring Soon', 12, false, '7b73cd1a-106f-50fb-a656-135af105df01', 'Kingsway House', 'B-002', 'B-002',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('916dbad4-1656-5dee-9cac-b422cfc56127', '7b73cd1a-106f-50fb-a656-135af105df01', 'compliance_certificate', 'EPC', 'EPC-B-002-004.pdf', '2022-03-03'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('af2be30c-7bd9-5da5-9d17-418de92a45c0', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EPC', 'EPC', 'EPC-B-002-004', 'EPC-B-002-004',
        'Building', 'UK', '2022-03-03', '2029-06-04', '2029-06-04',
        'Current', 1250, false, '7b73cd1a-106f-50fb-a656-135af105df01', 'Kingsway House', 'B-002', 'B-002',
        NULL, 'A. Surveyor', 'REG-B-002-004', '916dbad4-1656-5dee-9cac-b422cfc56127', '916dbad4-1656-5dee-9cac-b422cfc56127',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('c5b1756d-2542-5e9f-9b59-61b6fd7976f9', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY-B-002-005', 'ASBESTOS_SURVEY-B-002-005',
        'Building', 'UK', '2024-11-27', '2025-11-27', '2025-11-27',
        'Lapsed', -35, true, '7b73cd1a-106f-50fb-a656-135af105df01', 'Kingsway House', 'B-002', 'B-002',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('3609155d-ca67-56de-b788-ca259dac558f', '7b73cd1a-106f-50fb-a656-135af105df01', 'compliance_certificate', 'L8_RISK', 'L8_RISK-B-002-006.pdf', '2023-10-24'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('f6b334ed-39e7-5366-b152-82bbf7cb6355', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'L8_RISK', 'L8_RISK', 'L8_RISK-B-002-006', 'L8_RISK-B-002-006',
        'Building', 'UK', '2023-10-24', '2025-09-03', '2025-09-03',
        'Lapsed', -120, true, '7b73cd1a-106f-50fb-a656-135af105df01', 'Kingsway House', 'B-002', 'B-002',
        NULL, 'A. Surveyor', 'REG-B-002-006', '3609155d-ca67-56de-b788-ca259dac558f', '3609155d-ca67-56de-b788-ca259dac558f',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('2799e4e1-9027-53f3-a532-2137f3b0b621', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'compliance_certificate', 'FRA', 'FRA-B-003-000.pdf', '2025-11-02'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('eb8cb09f-bd9f-55d0-a1ab-79910c9299c8', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FRA', 'FRA', 'FRA-B-003-000', 'FRA-B-003-000',
        'Building', 'UK', '2025-11-02', '2026-11-02', '2026-11-02',
        'Current', 305, false, '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Town Hall', 'B-003', 'B-003',
        NULL, 'A. Surveyor', 'REG-B-003-000', '2799e4e1-9027-53f3-a532-2137f3b0b621', '2799e4e1-9027-53f3-a532-2137f3b0b621',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('9beb8a62-6fe9-5c4a-9ab8-256362c24b6f', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EICR', 'EICR', 'EICR-B-003-001', 'EICR-B-003-001',
        'Building', 'UK', '2024-11-27', '2029-11-21', '2029-11-21',
        'Current', 1420, false, '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Town Hall', 'B-003', 'B-003',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('0191a4a0-6109-5a6c-bf6d-d35ba677e0c5', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'compliance_certificate', 'CP17', 'CP17-B-003-002.pdf', '2025-03-07'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('22c395dc-209c-5edb-8e7d-67a57394027d', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'CP17', 'CP17', 'CP17-B-003-002', 'CP17-B-003-002',
        'Building', 'UK', '2025-03-07', '2026-03-07', '2026-03-07',
        'Due for Renewal', 65, false, '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Town Hall', 'B-003', 'B-003',
        NULL, 'A. Surveyor', 'REG-B-003-002', '0191a4a0-6109-5a6c-bf6d-d35ba677e0c5', '0191a4a0-6109-5a6c-bf6d-d35ba677e0c5',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('1c647673-6828-56eb-96fb-1f150e83ee26', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LOLER', 'LOLER', 'LOLER-B-003-003', 'LOLER-B-003-003',
        'Building', 'UK', '2025-07-15', '2026-01-13', '2026-01-13',
        'Expiring Soon', 12, false, '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Town Hall', 'B-003', 'B-003',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('5f27f18d-d82b-5071-8e11-c7308b07828f', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'compliance_certificate', 'EPC', 'EPC-B-003-004.pdf', '2022-03-03'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('da960f9e-6dae-539e-aae5-f1f8d517b564', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EPC', 'EPC', 'EPC-B-003-004', 'EPC-B-003-004',
        'Building', 'UK', '2022-03-03', '2029-06-04', '2029-06-04',
        'Current', 1250, false, '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Town Hall', 'B-003', 'B-003',
        NULL, 'A. Surveyor', 'REG-B-003-004', '5f27f18d-d82b-5071-8e11-c7308b07828f', '5f27f18d-d82b-5071-8e11-c7308b07828f',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('b17ed9fa-d9ac-5257-b96c-1fe4c50839e0', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY-B-003-005', 'ASBESTOS_SURVEY-B-003-005',
        'Building', 'UK', '2024-11-27', '2025-11-27', '2025-11-27',
        'Lapsed', -35, true, '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Town Hall', 'B-003', 'B-003',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('08967afe-767b-5cb1-b2d1-e9ea0d69fc5f', '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'compliance_certificate', 'L8_RISK', 'L8_RISK-B-003-006.pdf', '2023-10-24'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('7a2c435b-4815-5151-8402-80098826fb4c', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'L8_RISK', 'L8_RISK', 'L8_RISK-B-003-006', 'L8_RISK-B-003-006',
        'Building', 'UK', '2023-10-24', '2025-09-03', '2025-09-03',
        'Lapsed', -120, true, '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Town Hall', 'B-003', 'B-003',
        NULL, 'A. Surveyor', 'REG-B-003-006', '08967afe-767b-5cb1-b2d1-e9ea0d69fc5f', '08967afe-767b-5cb1-b2d1-e9ea0d69fc5f',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('a47ffaee-58c0-5459-b544-ab532da69913', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FIRE_ALARM_SERVICE', 'FIRE_ALARM_SERVICE', 'FIRE_ALARM_SERVICE-B-003-007', 'FIRE_ALARM_SERVICE-B-003-007',
        'Building', 'UK', '2025-09-03', '2026-03-04', '2026-03-04',
        'Due for Renewal', 62, false, '425f5590-404f-5d25-ac68-cac5c9cdebcc', 'Town Hall', 'B-003', 'B-003',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('4bb08766-d183-59a6-8964-c16ebd28515f', '3e058306-2411-592d-b063-2fb6028e7816', 'compliance_certificate', 'FRA', 'FRA-B-004-000.pdf', '2025-11-02'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('2fc47d7c-33f7-5833-b9ec-69a72f3d91c9', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FRA', 'FRA', 'FRA-B-004-000', 'FRA-B-004-000',
        'Building', 'UK', '2025-11-02', '2026-11-02', '2026-11-02',
        'Current', 305, false, '3e058306-2411-592d-b063-2fb6028e7816', 'Meridian Quay', 'B-004', 'B-004',
        NULL, 'A. Surveyor', 'REG-B-004-000', '4bb08766-d183-59a6-8964-c16ebd28515f', '4bb08766-d183-59a6-8964-c16ebd28515f',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('c0365760-4760-57d0-8a1d-81b71dbe7c32', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EICR', 'EICR', 'EICR-B-004-001', 'EICR-B-004-001',
        'Building', 'UK', '2024-11-27', '2029-11-21', '2029-11-21',
        'Current', 1420, false, '3e058306-2411-592d-b063-2fb6028e7816', 'Meridian Quay', 'B-004', 'B-004',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('748b3c99-1073-57fb-a6b6-0c441245fcfb', '3e058306-2411-592d-b063-2fb6028e7816', 'compliance_certificate', 'CP17', 'CP17-B-004-002.pdf', '2025-03-07'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('ae696fa0-5fb9-5134-9e58-ef00ef308b0b', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'CP17', 'CP17', 'CP17-B-004-002', 'CP17-B-004-002',
        'Building', 'UK', '2025-03-07', '2026-03-07', '2026-03-07',
        'Due for Renewal', 65, false, '3e058306-2411-592d-b063-2fb6028e7816', 'Meridian Quay', 'B-004', 'B-004',
        NULL, 'A. Surveyor', 'REG-B-004-002', '748b3c99-1073-57fb-a6b6-0c441245fcfb', '748b3c99-1073-57fb-a6b6-0c441245fcfb',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('9bdd0528-7ee3-5986-9787-6633430f5a64', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LOLER', 'LOLER', 'LOLER-B-004-003', 'LOLER-B-004-003',
        'Building', 'UK', '2025-07-15', '2026-01-13', '2026-01-13',
        'Expiring Soon', 12, false, '3e058306-2411-592d-b063-2fb6028e7816', 'Meridian Quay', 'B-004', 'B-004',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('ec2add04-8df7-53bd-80fb-36cab5533131', '3e058306-2411-592d-b063-2fb6028e7816', 'compliance_certificate', 'EPC', 'EPC-B-004-004.pdf', '2022-03-03'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('d136a06d-e584-586b-9090-0a84f36e438e', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EPC', 'EPC', 'EPC-B-004-004', 'EPC-B-004-004',
        'Building', 'UK', '2022-03-03', '2029-06-04', '2029-06-04',
        'Current', 1250, false, '3e058306-2411-592d-b063-2fb6028e7816', 'Meridian Quay', 'B-004', 'B-004',
        NULL, 'A. Surveyor', 'REG-B-004-004', 'ec2add04-8df7-53bd-80fb-36cab5533131', 'ec2add04-8df7-53bd-80fb-36cab5533131',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('09a67c09-2d42-5472-a25c-a30b16fcd2db', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY-B-004-005', 'ASBESTOS_SURVEY-B-004-005',
        'Building', 'UK', '2024-11-27', '2025-11-27', '2025-11-27',
        'Lapsed', -35, true, '3e058306-2411-592d-b063-2fb6028e7816', 'Meridian Quay', 'B-004', 'B-004',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('831a6523-15f2-5786-b35c-d82b53f2011e', '3e058306-2411-592d-b063-2fb6028e7816', 'compliance_certificate', 'L8_RISK', 'L8_RISK-B-004-006.pdf', '2023-10-24'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('533daff0-42d1-5366-a841-ba8a240770f7', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'L8_RISK', 'L8_RISK', 'L8_RISK-B-004-006', 'L8_RISK-B-004-006',
        'Building', 'UK', '2023-10-24', '2025-09-03', '2025-09-03',
        'Lapsed', -120, true, '3e058306-2411-592d-b063-2fb6028e7816', 'Meridian Quay', 'B-004', 'B-004',
        NULL, 'A. Surveyor', 'REG-B-004-006', '831a6523-15f2-5786-b35c-d82b53f2011e', '831a6523-15f2-5786-b35c-d82b53f2011e',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('086b9a2f-3a01-581e-ab1b-e813d4b91555', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FIRE_ALARM_SERVICE', 'FIRE_ALARM_SERVICE', 'FIRE_ALARM_SERVICE-B-004-007', 'FIRE_ALARM_SERVICE-B-004-007',
        'Building', 'UK', '2025-09-03', '2026-03-04', '2026-03-04',
        'Due for Renewal', 62, false, '3e058306-2411-592d-b063-2fb6028e7816', 'Meridian Quay', 'B-004', 'B-004',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('7e1d56da-45b2-5c8b-847a-b92616dd45b5', '3e058306-2411-592d-b063-2fb6028e7816', 'compliance_certificate', 'EMERGENCY_LIGHTING', 'EMERGENCY_LIGHTING-B-004-008.pdf', '2025-12-12'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('b7af5f2c-aeb8-599e-8a3d-e753c0fb191c', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EMERGENCY_LIGHTING', 'EMERGENCY_LIGHTING', 'EMERGENCY_LIGHTING-B-004-008', 'EMERGENCY_LIGHTING-B-004-008',
        'Building', 'UK', '2025-12-12', '2026-01-11', '2026-01-11',
        'Expiring Soon', 10, false, '3e058306-2411-592d-b063-2fb6028e7816', 'Meridian Quay', 'B-004', 'B-004',
        NULL, 'A. Surveyor', 'REG-B-004-008', '7e1d56da-45b2-5c8b-847a-b92616dd45b5', '7e1d56da-45b2-5c8b-847a-b92616dd45b5',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('b5b2d055-b6b9-5bc4-a2e7-33c4591fb8c9', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'compliance_certificate', 'FRA', 'FRA-B-005-000.pdf', '2025-11-02'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('e919f72e-e228-5971-99c6-8d43a9141732', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FRA', 'FRA', 'FRA-B-005-000', 'FRA-B-005-000',
        'Building', 'UK', '2025-11-02', '2026-11-02', '2026-11-02',
        'Current', 305, false, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, 'A. Surveyor', 'REG-B-005-000', 'b5b2d055-b6b9-5bc4-a2e7-33c4591fb8c9', 'b5b2d055-b6b9-5bc4-a2e7-33c4591fb8c9',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('ca318091-6518-54c6-9037-0f6841a0c630', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EICR', 'EICR', 'EICR-B-005-001', 'EICR-B-005-001',
        'Building', 'UK', '2024-11-27', '2029-11-21', '2029-11-21',
        'Current', 1420, false, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('aaac6170-e5fc-5b7d-9728-697c8b7fadb8', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'compliance_certificate', 'CP17', 'CP17-B-005-002.pdf', '2025-03-07'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('7c1ce6cc-db11-595c-851f-02999dde4fa0', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'CP17', 'CP17', 'CP17-B-005-002', 'CP17-B-005-002',
        'Building', 'UK', '2025-03-07', '2026-03-07', '2026-03-07',
        'Due for Renewal', 65, false, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, 'A. Surveyor', 'REG-B-005-002', 'aaac6170-e5fc-5b7d-9728-697c8b7fadb8', 'aaac6170-e5fc-5b7d-9728-697c8b7fadb8',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('ff50c30c-482e-5f7a-aa3f-bd8700b5c764', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LOLER', 'LOLER', 'LOLER-B-005-003', 'LOLER-B-005-003',
        'Building', 'UK', '2025-07-15', '2026-01-13', '2026-01-13',
        'Expiring Soon', 12, false, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('5bca946e-e16b-55a9-9b2b-328f4c810463', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'compliance_certificate', 'EPC', 'EPC-B-005-004.pdf', '2022-03-03'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('8fb468c3-249d-546e-a638-18424f42c1ec', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EPC', 'EPC', 'EPC-B-005-004', 'EPC-B-005-004',
        'Building', 'UK', '2022-03-03', '2029-06-04', '2029-06-04',
        'Current', 1250, false, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, 'A. Surveyor', 'REG-B-005-004', '5bca946e-e16b-55a9-9b2b-328f4c810463', '5bca946e-e16b-55a9-9b2b-328f4c810463',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('0b308aa7-5365-5f8c-a66f-3117d960f990', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY-B-005-005', 'ASBESTOS_SURVEY-B-005-005',
        'Building', 'UK', '2024-11-27', '2025-11-27', '2025-11-27',
        'Lapsed', -35, true, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('042e7ef0-b994-5437-b9a8-746474a4dbfc', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'compliance_certificate', 'L8_RISK', 'L8_RISK-B-005-006.pdf', '2023-10-24'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('e7644915-014c-51c0-82ef-809280c2703d', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'L8_RISK', 'L8_RISK', 'L8_RISK-B-005-006', 'L8_RISK-B-005-006',
        'Building', 'UK', '2023-10-24', '2025-09-03', '2025-09-03',
        'Lapsed', -120, true, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, 'A. Surveyor', 'REG-B-005-006', '042e7ef0-b994-5437-b9a8-746474a4dbfc', '042e7ef0-b994-5437-b9a8-746474a4dbfc',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('8f661520-f40a-59b9-9527-987d7ffc1317', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FIRE_ALARM_SERVICE', 'FIRE_ALARM_SERVICE', 'FIRE_ALARM_SERVICE-B-005-007', 'FIRE_ALARM_SERVICE-B-005-007',
        'Building', 'UK', '2025-09-03', '2026-03-04', '2026-03-04',
        'Due for Renewal', 62, false, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('7cf2bc03-f979-584d-a617-230539356d4d', 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'compliance_certificate', 'EMERGENCY_LIGHTING', 'EMERGENCY_LIGHTING-B-005-008.pdf', '2025-12-12'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('be810ee5-f695-5b8c-924e-0a42450443bc', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EMERGENCY_LIGHTING', 'EMERGENCY_LIGHTING', 'EMERGENCY_LIGHTING-B-005-008', 'EMERGENCY_LIGHTING-B-005-008',
        'Building', 'UK', '2025-12-12', '2026-01-11', '2026-01-11',
        'Expiring Soon', 10, false, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, 'A. Surveyor', 'REG-B-005-008', '7cf2bc03-f979-584d-a617-230539356d4d', '7cf2bc03-f979-584d-a617-230539356d4d',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('42bf8a1b-55a2-5efa-a010-69e233685fc9', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'PAT', 'PAT', 'PAT-B-005-009', 'PAT-B-005-009',
        'Building', 'UK', '2025-06-15', '2026-06-15', '2026-06-15',
        'Current', 165, false, 'fe259daf-58b9-58be-b05e-4e0da3bc0d08', 'AN Other House', 'B-005', 'B-005',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('5560d483-21e7-566c-8145-804a72eb33a9', 'f12e9629-95ed-5722-80cc-a1397f627850', 'compliance_certificate', 'FRA', 'FRA-B-006-000.pdf', '2025-11-02'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('732e7fb9-d045-5d60-a754-828b897a0ec5', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FRA', 'FRA', 'FRA-B-006-000', 'FRA-B-006-000',
        'Building', 'UK', '2025-11-02', '2026-11-02', '2026-11-02',
        'Current', 305, false, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, 'A. Surveyor', 'REG-B-006-000', '5560d483-21e7-566c-8145-804a72eb33a9', '5560d483-21e7-566c-8145-804a72eb33a9',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('48380155-0ad0-5de3-ab27-a8ff99b66053', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EICR', 'EICR', 'EICR-B-006-001', 'EICR-B-006-001',
        'Building', 'UK', '2024-11-27', '2029-11-21', '2029-11-21',
        'Current', 1420, false, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('d6d7fa16-ccf7-5f41-bdae-43ee5ca57042', 'f12e9629-95ed-5722-80cc-a1397f627850', 'compliance_certificate', 'CP17', 'CP17-B-006-002.pdf', '2025-03-07'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('b841c26c-0596-51cc-bbcc-52ac3b4f60af', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'CP17', 'CP17', 'CP17-B-006-002', 'CP17-B-006-002',
        'Building', 'UK', '2025-03-07', '2026-03-07', '2026-03-07',
        'Due for Renewal', 65, false, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, 'A. Surveyor', 'REG-B-006-002', 'd6d7fa16-ccf7-5f41-bdae-43ee5ca57042', 'd6d7fa16-ccf7-5f41-bdae-43ee5ca57042',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('e4a47f35-59c3-5524-8135-35b8321fa443', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LOLER', 'LOLER', 'LOLER-B-006-003', 'LOLER-B-006-003',
        'Building', 'UK', '2025-07-15', '2026-01-13', '2026-01-13',
        'Expiring Soon', 12, false, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('8744ea96-f76e-53eb-a5e0-adde8eaa4f7e', 'f12e9629-95ed-5722-80cc-a1397f627850', 'compliance_certificate', 'EPC', 'EPC-B-006-004.pdf', '2022-03-03'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('480a26f0-5d46-5b63-a844-5c15bb960cd1', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EPC', 'EPC', 'EPC-B-006-004', 'EPC-B-006-004',
        'Building', 'UK', '2022-03-03', '2029-06-04', '2029-06-04',
        'Current', 1250, false, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, 'A. Surveyor', 'REG-B-006-004', '8744ea96-f76e-53eb-a5e0-adde8eaa4f7e', '8744ea96-f76e-53eb-a5e0-adde8eaa4f7e',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('26003419-4629-5319-b485-2f11a46e3395', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY', 'ASBESTOS_SURVEY-B-006-005', 'ASBESTOS_SURVEY-B-006-005',
        'Building', 'UK', '2024-11-27', '2025-11-27', '2025-11-27',
        'Lapsed', -35, true, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('1ad28f3b-4db7-5cd2-9dbf-3e5721462878', 'f12e9629-95ed-5722-80cc-a1397f627850', 'compliance_certificate', 'L8_RISK', 'L8_RISK-B-006-006.pdf', '2023-10-24'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('0a07464c-5c08-5339-bdcd-52e1847e184a', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'L8_RISK', 'L8_RISK', 'L8_RISK-B-006-006', 'L8_RISK-B-006-006',
        'Building', 'UK', '2023-10-24', '2025-09-03', '2025-09-03',
        'Lapsed', -120, true, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, 'A. Surveyor', 'REG-B-006-006', '1ad28f3b-4db7-5cd2-9dbf-3e5721462878', '1ad28f3b-4db7-5cd2-9dbf-3e5721462878',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('c49c93ae-de0b-599a-b991-9a1a4699b6cf', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FIRE_ALARM_SERVICE', 'FIRE_ALARM_SERVICE', 'FIRE_ALARM_SERVICE-B-006-007', 'FIRE_ALARM_SERVICE-B-006-007',
        'Building', 'UK', '2025-09-03', '2026-03-04', '2026-03-04',
        'Due for Renewal', 62, false, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('9644d317-1848-50fd-9c87-5b4021709309', 'f12e9629-95ed-5722-80cc-a1397f627850', 'compliance_certificate', 'EMERGENCY_LIGHTING', 'EMERGENCY_LIGHTING-B-006-008.pdf', '2025-12-12'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('f6939369-5a8d-5455-bcda-e0b03a4026f1', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'EMERGENCY_LIGHTING', 'EMERGENCY_LIGHTING', 'EMERGENCY_LIGHTING-B-006-008', 'EMERGENCY_LIGHTING-B-006-008',
        'Building', 'UK', '2025-12-12', '2026-01-11', '2026-01-11',
        'Expiring Soon', 10, false, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, 'A. Surveyor', 'REG-B-006-008', '9644d317-1848-50fd-9c87-5b4021709309', '9644d317-1848-50fd-9c87-5b4021709309',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('3084179c-851b-5ae2-97ca-36de104385fe', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'PAT', 'PAT', 'PAT-B-006-009', 'PAT-B-006-009',
        'Building', 'UK', '2025-06-15', '2026-06-15', '2026-06-15',
        'Current', 165, false, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('7a9505a5-6949-5c7a-8c10-e325c9a42f77', 'f12e9629-95ed-5722-80cc-a1397f627850', 'compliance_certificate', 'TM44', 'TM44-B-006-010.pdf', '2023-07-16'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('6f20c692-236c-545c-8e63-b146f3b3c90b', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'TM44', 'TM44', 'TM44-B-006-010', 'TM44-B-006-010',
        'Building', 'UK', '2023-07-16', '2028-07-09', '2028-07-09',
        'Current', 920, false, 'f12e9629-95ed-5722-80cc-a1397f627850', 'Riverside Court', 'B-006', 'B-006',
        NULL, 'A. Surveyor', 'REG-B-006-010', '7a9505a5-6949-5c7a-8c10-e325c9a42f77', '7a9505a5-6949-5c7a-8c10-e325c9a42f77',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('c3d76345-81f0-58d7-a9c4-637ea1f7545e', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'compliance_certificate', 'FIRE_SAFETY_CERT', 'FIRE_SAFETY_CERT-B-007-000.pdf', '2025-06-15'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('ef917d3b-41e1-5565-b815-915cf693ce86', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FIRE_SAFETY_CERT', 'FIRE_SAFETY_CERT', 'FIRE_SAFETY_CERT-B-007-000', 'FIRE_SAFETY_CERT-B-007-000',
        'Building', 'UAE', '2025-06-15', '2026-06-10', '2026-06-10',
        'Current', 160, false, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, 'A. Surveyor', 'REG-B-007-000', 'c3d76345-81f0-58d7-a9c4-637ea1f7545e', 'c3d76345-81f0-58d7-a9c4-637ea1f7545e',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('1c427980-e520-5113-a320-15b58cdb1c78', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FIRE_ALARM_TEST', 'FIRE_ALARM_TEST', 'FIRE_ALARM_TEST-B-007-001', 'FIRE_ALARM_TEST-B-007-001',
        'Building', 'UAE', '2025-09-23', '2026-03-22', '2026-03-22',
        'Due for Renewal', 80, false, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('f3b397f5-fb9d-53f0-bedf-3ad709b97cd3', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'compliance_certificate', 'FIRE_PUMP', 'FIRE_PUMP-B-007-002.pdf', '2025-09-03'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('5ec3c3b4-e942-5874-b706-befe091e3299', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'FIRE_PUMP', 'FIRE_PUMP', 'FIRE_PUMP-B-007-002', 'FIRE_PUMP-B-007-002',
        'Building', 'UAE', '2025-09-03', '2026-03-02', '2026-03-02',
        'Due for Renewal', 60, false, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, 'A. Surveyor', 'REG-B-007-002', 'f3b397f5-fb9d-53f0-bedf-3ad709b97cd3', 'f3b397f5-fb9d-53f0-bedf-3ad709b97cd3',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('179f673e-9924-56cd-adb5-557dcd28ae82', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'SPRINKLER_TEST', 'SPRINKLER_TEST', 'SPRINKLER_TEST-B-007-003', 'SPRINKLER_TEST-B-007-003',
        'Building', 'UAE', '2025-08-04', '2026-01-31', '2026-01-31',
        'Due for Renewal', 30, false, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('85290686-2bcb-5aa3-88e3-ee3cb5870a9f', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'compliance_certificate', 'LIFT_INSPECTION', 'LIFT_INSPECTION-B-007-004.pdf', '2025-03-07'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('ab4f3222-9644-5093-bb1f-037d14247581', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LIFT_INSPECTION', 'LIFT_INSPECTION', 'LIFT_INSPECTION-B-007-004', 'LIFT_INSPECTION-B-007-004',
        'Building', 'UAE', '2025-03-07', '2025-12-12', '2025-12-12',
        'Lapsed', -20, true, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, 'A. Surveyor', 'REG-B-007-004', '85290686-2bcb-5aa3-88e3-ee3cb5870a9f', '85290686-2bcb-5aa3-88e3-ee3cb5870a9f',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('b3454820-4c86-5de5-a5d0-8789f7982676', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'COOLING_TOWER', 'COOLING_TOWER', 'COOLING_TOWER-B-007-005', 'COOLING_TOWER-B-007-005',
        'Building', 'UAE', '2025-11-02', '2026-01-26', '2026-01-26',
        'Expiring Soon', 25, false, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('4776f790-fb07-53c6-b585-53412d106bad', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'compliance_certificate', 'WATER_TANK_CLEANING', 'WATER_TANK_CLEANING-B-007-006.pdf', '2025-07-05'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('f9a6a1d5-76e4-58a9-97aa-ba6f716db807', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'WATER_TANK_CLEANING', 'WATER_TANK_CLEANING', 'WATER_TANK_CLEANING-B-007-006', 'WATER_TANK_CLEANING-B-007-006',
        'Building', 'UAE', '2025-07-05', '2025-12-22', '2025-12-22',
        'Lapsed', -10, true, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, 'A. Surveyor', 'REG-B-007-006', '4776f790-fb07-53c6-b585-53412d106bad', '4776f790-fb07-53c6-b585-53412d106bad',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('39e77ae4-5e81-5365-bba9-4905a5637efa', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'IAQ_TEST', 'IAQ_TEST', 'IAQ_TEST-B-007-007', 'IAQ_TEST-B-007-007',
        'Building', 'UAE', '2025-10-03', '2026-09-28', '2026-09-28',
        'Current', 270, false, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('4230dc34-8eb7-57c7-8e4b-28eb988d1add', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'compliance_certificate', 'DUCT_CLEANING', 'DUCT_CLEANING-B-007-008.pdf', '2024-11-27'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('c81e9d03-ca83-5ead-ae6c-47c739e28900', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'DUCT_CLEANING', 'DUCT_CLEANING', 'DUCT_CLEANING-B-007-008', 'DUCT_CLEANING-B-007-008',
        'Building', 'UAE', '2024-11-27', '2025-11-22', '2025-11-22',
        'Lapsed', -40, true, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, 'A. Surveyor', 'REG-B-007-008', '4230dc34-8eb7-57c7-8e4b-28eb988d1add', '4230dc34-8eb7-57c7-8e4b-28eb988d1add',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('facdc2eb-3294-5642-a7ba-c606e3dcdcc0', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'PEST_CONTROL', 'PEST_CONTROL', 'PEST_CONTROL-B-007-009', 'PEST_CONTROL-B-007-009',
        'Building', 'UAE', '2025-12-02', '2026-03-02', '2026-03-02',
        'Due for Renewal', 60, false, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('87b03635-8356-582e-95f2-cbebd3d3ed66', '8f4a8708-a87c-5887-99b5-865c3a98b511', 'compliance_certificate', 'PUBLIC_LIABILITY', 'PUBLIC_LIABILITY-B-007-010.pdf', '2025-10-13'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('c719d251-1279-5b4a-aa8a-4fbc31321b8f', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'PUBLIC_LIABILITY', 'PUBLIC_LIABILITY', 'PUBLIC_LIABILITY-B-007-010', 'PUBLIC_LIABILITY-B-007-010',
        'Building', 'UAE', '2025-10-13', '2026-10-13', '2026-10-13',
        'Current', 285, false, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, 'A. Surveyor', 'REG-B-007-010', '87b03635-8356-582e-95f2-cbebd3d3ed66', '87b03635-8356-582e-95f2-cbebd3d3ed66',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('782a7a96-1054-5e77-b058-9a39f83468d2', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'THERMOGRAPHY', 'THERMOGRAPHY', 'THERMOGRAPHY-B-007-011', 'THERMOGRAPHY-B-007-011',
        'Building', 'UAE', '2025-04-26', '2026-04-21', '2026-04-21',
        'Current', 110, false, '8f4a8708-a87c-5887-99b5-865c3a98b511', 'Marina Heights', 'B-007', 'B-007',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('468315dc-ba6e-5b6e-aa28-1d56dfc91e93', '954c3214-8467-528f-95c6-a320de9f579d', 'compliance_certificate', 'US_SPRINKLER_NFPA25', 'US_SPRINKLER_NFPA25-B-008-000.pdf', '2025-09-23'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('c28ba357-2742-55be-b7aa-a3dc5689a69e', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_SPRINKLER_NFPA25', 'US_SPRINKLER_NFPA25', 'US_SPRINKLER_NFPA25-B-008-000', 'US_SPRINKLER_NFPA25-B-008-000',
        'Building', 'US', '2025-09-23', '2026-09-23', '2026-09-23',
        'Current', 265, false, '954c3214-8467-528f-95c6-a320de9f579d', 'Northgate Mall', 'B-008', 'B-008',
        NULL, 'A. Surveyor', 'REG-B-008-000', '468315dc-ba6e-5b6e-aa28-1d56dfc91e93', '468315dc-ba6e-5b6e-aa28-1d56dfc91e93',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('4de069dd-e059-569e-8e17-4aed1e6c1751', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_FIRE_ALARM_NFPA72', 'US_FIRE_ALARM_NFPA72', 'US_FIRE_ALARM_NFPA72-B-008-001', 'US_FIRE_ALARM_NFPA72-B-008-001',
        'Building', 'US', '2025-06-15', '2026-06-15', '2026-06-15',
        'Current', 165, false, '954c3214-8467-528f-95c6-a320de9f579d', 'Northgate Mall', 'B-008', 'B-008',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('9f57abe3-3f11-51c5-9a70-f9d75ba67c4d', '954c3214-8467-528f-95c6-a320de9f579d', 'compliance_certificate', 'US_ELEVATOR_A17', 'US_ELEVATOR_A17-B-008-002.pdf', '2025-03-07'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('be2dc25b-6140-5cd4-abc5-ccc6f26b290d', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_ELEVATOR_A17', 'US_ELEVATOR_A17', 'US_ELEVATOR_A17-B-008-002', 'US_ELEVATOR_A17-B-008-002',
        'Building', 'US', '2025-03-07', '2026-03-07', '2026-03-07',
        'Due for Renewal', 65, false, '954c3214-8467-528f-95c6-a320de9f579d', 'Northgate Mall', 'B-008', 'B-008',
        NULL, 'A. Surveyor', 'REG-B-008-002', '9f57abe3-3f11-51c5-9a70-f9d75ba67c4d', '9f57abe3-3f11-51c5-9a70-f9d75ba67c4d',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('48d768f7-583b-5cf3-bef9-0e97e952c412', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_BOILER_INSPECTION', 'US_BOILER_INSPECTION', 'US_BOILER_INSPECTION-B-008-003', 'US_BOILER_INSPECTION-B-008-003',
        'Building', 'US', '2024-12-17', '2025-12-17', '2025-12-17',
        'Lapsed', -15, true, '954c3214-8467-528f-95c6-a320de9f579d', 'Northgate Mall', 'B-008', 'B-008',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('121ec594-9355-5ffe-ac2b-888597b07279', '954c3214-8467-528f-95c6-a320de9f579d', 'compliance_certificate', 'US_BACKFLOW', 'US_BACKFLOW-B-008-004.pdf', '2025-01-26'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('91291377-7995-596b-83c0-2ac8c294ea4d', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_BACKFLOW', 'US_BACKFLOW', 'US_BACKFLOW-B-008-004', 'US_BACKFLOW-B-008-004',
        'Building', 'US', '2025-01-26', '2026-01-26', '2026-01-26',
        'Expiring Soon', 25, false, '954c3214-8467-528f-95c6-a320de9f579d', 'Northgate Mall', 'B-008', 'B-008',
        NULL, 'A. Surveyor', 'REG-B-008-004', '121ec594-9355-5ffe-ac2b-888597b07279', '121ec594-9355-5ffe-ac2b-888597b07279',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('adb48f4d-66e0-5b0f-93f7-ec9217747217', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_FIRE_EXT_NFPA10', 'US_FIRE_EXT_NFPA10', 'US_FIRE_EXT_NFPA10-B-008-005', 'US_FIRE_EXT_NFPA10-B-008-005',
        'Building', 'US', '2025-11-02', '2026-11-02', '2026-11-02',
        'Current', 305, false, '954c3214-8467-528f-95c6-a320de9f579d', 'Northgate Mall', 'B-008', 'B-008',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('deb6ddc4-c641-5ac4-8a62-fb7b17652bd3', '954c3214-8467-528f-95c6-a320de9f579d', 'compliance_certificate', 'US_EMERGENCY_LIGHT_NFPA101', 'US_EMERGENCY_LIGHT_NFPA101-B-008-006.pdf', '2025-12-12'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('ace9f45e-15c4-5e9a-9381-83f65a0ebd61', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_EMERGENCY_LIGHT_NFPA101', 'US_EMERGENCY_LIGHT_NFPA101', 'US_EMERGENCY_LIGHT_NFPA101-B-008-006', 'US_EMERGENCY_LIGHT_NFPA101-B-008-006',
        'Building', 'US', '2025-12-12', '2026-01-11', '2026-01-11',
        'Expiring Soon', 10, false, '954c3214-8467-528f-95c6-a320de9f579d', 'Northgate Mall', 'B-008', 'B-008',
        NULL, 'A. Surveyor', 'REG-B-008-006', 'deb6ddc4-c641-5ac4-8a62-fb7b17652bd3', 'deb6ddc4-c641-5ac4-8a62-fb7b17652bd3',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('1355e4eb-2e1c-5405-94f8-2253b9f22933', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_GENERATOR_NFPA110', 'US_GENERATOR_NFPA110', 'US_GENERATOR_NFPA110-B-008-007', 'US_GENERATOR_NFPA110-B-008-007',
        'Building', 'US', '2025-08-04', '2025-12-24', '2025-12-24',
        'Lapsed', -8, true, '954c3214-8467-528f-95c6-a320de9f579d', 'Northgate Mall', 'B-008', 'B-008',
        NULL, NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO documents (document_id, building_id, doc_type, title, file_name, uploaded_at) VALUES ('141c186b-9322-5fce-b07c-a8c41d5f97c4', '954c3214-8467-528f-95c6-a320de9f579d', 'compliance_certificate', 'US_LEGIONELLA_ASHRAE188', 'US_LEGIONELLA_ASHRAE188-B-008-008.pdf', '2024-11-27'::timestamptz) ON CONFLICT (document_id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('ac3bac9c-24d3-5072-b769-95efa8a3740c', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_LEGIONELLA_ASHRAE188', 'US_LEGIONELLA_ASHRAE188', 'US_LEGIONELLA_ASHRAE188-B-008-008', 'US_LEGIONELLA_ASHRAE188-B-008-008',
        'Building', 'US', '2024-11-27', '2026-11-27', '2026-11-27',
        'Current', 330, false, '954c3214-8467-528f-95c6-a320de9f579d', 'Northgate Mall', 'B-008', 'B-008',
        NULL, 'A. Surveyor', 'REG-B-008-008', '141c186b-9322-5fce-b07c-a8c41d5f97c4', '141c186b-9322-5fce-b07c-a8c41d5f97c4',
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
-- B-009 Raffles Link: no SG pack exists, so nothing can be scored against one.

-- Vendor scope: accreditations the firms hold, one lapsed each so the blocked path
-- on the vendor rail has something real behind it.
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('cc5c21e2-2c10-548b-8e65-25c778927341', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LEIA', 'LEIA', 'LEIA-APEX-00', 'LEIA-APEX-00',
        'Vendor', 'UK', '2025-03-07', '2026-07-20', '2026-07-20',
        'Current', 200, false, NULL, NULL, NULL, NULL,
        '91bba149-25cb-546b-b349-b14a2fab86c7', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('b998150f-72b9-5581-91e2-3738fbfa6439', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LOLER_CP', 'LOLER_CP', 'LOLER_CP-APEX-01', 'LOLER_CP-APEX-01',
        'Vendor', 'UK', '2025-06-15', '2025-12-10', '2025-12-10',
        'Lapsed', -22, true, NULL, NULL, NULL, NULL,
        '91bba149-25cb-546b-b349-b14a2fab86c7', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('d1ae17ea-1963-552d-8912-a1cf35314e2e', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'CHAS_SSIP', 'CHAS_SSIP', 'CHAS_SSIP-APEX-02', 'CHAS_SSIP-APEX-02',
        'Vendor', 'UK', '2025-09-23', '2026-10-08', '2026-10-08',
        'Current', 280, false, NULL, NULL, NULL, NULL,
        '91bba149-25cb-546b-b349-b14a2fab86c7', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('99e74d79-dcba-5b96-902d-503a649da6a1', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'LCA', 'LCA', 'LCA-CLRW-00', 'LCA-CLRW-00',
        'Vendor', 'UK', '2025-04-26', '2026-06-10', '2026-06-10',
        'Current', 160, false, NULL, NULL, NULL, NULL,
        '3cf6cc98-9f75-580f-88e5-27c29631945f', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('053d0253-511d-5c03-bc43-4667776bfcb2', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'ISO_9001', 'ISO_9001', 'ISO_9001-CLRW-01', 'ISO_9001-CLRW-01',
        'Vendor', 'UK', '2024-11-27', '2026-11-17', '2026-11-17',
        'Current', 320, false, NULL, NULL, NULL, NULL,
        '3cf6cc98-9f75-580f-88e5-27c29631945f', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('c6a7aad7-6193-5569-8a90-22b41392dfb3', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'PUBLIC_LIABILITY', 'PUBLIC_LIABILITY', 'PUBLIC_LIABILITY-GULF-00', 'PUBLIC_LIABILITY-GULF-00',
        'Vendor', 'UAE', '2025-07-05', '2026-07-10', '2026-07-10',
        'Current', 190, false, NULL, NULL, NULL, NULL,
        'aa4f1e43-501c-56b8-8d13-6cac6902d6c2', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('96bb26b5-4a60-5512-9b4b-94e8c77d02b7', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'US_STATE_CONTRACTOR_LICENSE', 'US_STATE_CONTRACTOR_LICENSE', 'US_STATE_CONTRACTOR_LICENSE-NGEL-00', 'US_STATE_CONTRACTOR_LICENSE-NGEL-00',
        'Vendor', 'US', '2025-03-07', '2026-08-29', '2026-08-29',
        'Current', 240, false, NULL, NULL, NULL, NULL,
        'f0d562e2-dab9-53a3-ad79-73a970da2417', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('8fcda2e1-7aa4-506c-bfaa-887cb48cf84a', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'BAFE_SP203_1', 'BAFE_SP203_1', 'BAFE_SP203_1-SENT-00', 'BAFE_SP203_1-SENT-00',
        'Vendor', 'UK', '2025-05-26', '2025-12-18', '2025-12-18',
        'Lapsed', -14, true, NULL, NULL, NULL, NULL,
        '4221732c-f44d-537c-8c95-76299a19dfef', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('5aaca35d-2690-539c-b48c-1af5739c8be7', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'NSI_GOLD_FIRE', 'NSI_GOLD_FIRE', 'NSI_GOLD_FIRE-SENT-01', 'NSI_GOLD_FIRE-SENT-01',
        'Vendor', 'UK', '2025-08-04', '2026-07-30', '2026-07-30',
        'Current', 210, false, NULL, NULL, NULL, NULL,
        '4221732c-f44d-537c-8c95-76299a19dfef', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('6e81bdcd-9075-5905-9aef-d39ba037df84', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'GAS_SAFE', 'GAS_SAFE', 'GAS_SAFE-HALD-00', 'GAS_SAFE-HALD-00',
        'Vendor', 'UK', '2025-10-03', '2026-10-03', '2026-10-03',
        'Current', 275, false, NULL, NULL, NULL, NULL,
        '3f07cc45-fa35-52be-9c80-154df200ece9', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;
INSERT INTO compliance_certificates (id, org_id, organization_id, cert_type,
        certificate_type_code, certificate_number, certificate_ref, cert_scope,
        country_code, issue_date, expiry_date, next_due_date, status, days_to_expiry,
        insurance_risk_flag, building_id, building_name, building_reference, site_ref,
        vendor_id, inspector_name, inspector_accreditation_number, document_id,
        source_document_id, raw_metadata)
    VALUES ('17d4dc9a-a6f4-5e4b-9ab8-346bec2833c4', '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'REFCOM', 'REFCOM', 'REFCOM-HALD-01', 'REFCOM-HALD-01',
        'Vendor', 'UK', '2025-04-16', '2026-04-16', '2026-04-16',
        'Current', 105, false, NULL, NULL, NULL, NULL,
        '3f07cc45-fa35-52be-9c80-154df200ece9', NULL, NULL, NULL, NULL,
        '{"demo": true, "source_system": "04_demo_data.sql"}'::jsonb)
    ON CONFLICT (id) DO NOTHING;

-- ── accounts ──
--
-- These CANNOT sign in, deliberately: their password hash is not a hash of anything. A
-- demo account with a known password is a back door, because this file is public and so
-- is the password. They exist to exercise the account list, the platform_role gates, and
-- the fact that a job title and a platform role are two different things — Rowan is a
-- Facilities Director who is also an admin here; Chris is an HVAC Specialist who is not.
--
-- For an account you can use, register one:
--   POST /api/auth/register {"email": "...", "password": "a-long-enough-passphrase"}
INSERT INTO users (id, organization_id, full_name, email, password_hash, role, platform_role, status, email_verified) VALUES ('25a4e991-8211-5169-a096-24ade73bd07e', '00000000-0000-0000-0000-000000000001', 'Rowan Ellis', 'ops.director@example.com', '!no-password-set-demo-row', 'Facilities Director', 'admin', 'invited', false) ON CONFLICT (id) DO NOTHING;
INSERT INTO users (id, organization_id, full_name, email, password_hash, role, platform_role, status, email_verified) VALUES ('0d9ab80f-f7bf-56b6-a796-0f6aa32e0dc9', '00000000-0000-0000-0000-000000000001', 'Sam Okafor', 'fm.london@example.com', '!no-password-set-demo-row', 'Facility Manager', 'user', 'invited', false) ON CONFLICT (id) DO NOTHING;
INSERT INTO users (id, organization_id, full_name, email, password_hash, role, platform_role, status, email_verified) VALUES ('b9f48d75-2d7a-5d02-96fd-9e411e684488', '00000000-0000-0000-0000-000000000001', 'Priya Raman', 'fm.manchester@example.com', '!no-password-set-demo-row', 'Maintenance Supervisor', 'user', 'invited', false) ON CONFLICT (id) DO NOTHING;
INSERT INTO users (id, organization_id, full_name, email, password_hash, role, platform_role, status, email_verified) VALUES ('01ad2dcc-2ccf-571e-a3db-78a432c62b72', '00000000-0000-0000-0000-000000000001', 'Jo Whitfield', 'planner@example.com', '!no-password-set-demo-row', 'Maintenance Planner', 'user', 'invited', false) ON CONFLICT (id) DO NOTHING;
INSERT INTO users (id, organization_id, full_name, email, password_hash, role, platform_role, status, email_verified) VALUES ('ef35db47-2b5f-5dc8-b167-e18b16ae4a74', '00000000-0000-0000-0000-000000000001', 'Chris Nakamura', 'hvac.lead@example.com', '!no-password-set-demo-row', 'HVAC Specialist', 'user', 'invited', false) ON CONFLICT (id) DO NOTHING;
INSERT INTO users (id, organization_id, full_name, email, password_hash, role, platform_role, status, email_verified) VALUES ('55b9c4fb-91e1-5ce7-9cbb-0b8367b80184', '00000000-0000-0000-0000-000000000001', 'Dana Ilyas', 'inspector@example.com', '!no-password-set-demo-row', 'Quality Inspector', 'user', 'invited', false) ON CONFLICT (id) DO NOTHING;

COMMIT;

-- What you should see:
--
--   SELECT count(*) FROM buildings;                       -- 9
--   SELECT country_code, count(*) FROM compliance_certificates
--     WHERE cert_scope = 'Building' GROUP BY 1;           -- UK, UAE, US
--   SELECT role, platform_role FROM users;                -- job title vs platform role
--   SELECT count(*) FROM documents WHERE file_name IS NOT NULL;
--
-- Raffles Link holds no certificates: there is no Singapore pack, and inventing one would
-- put certificate types in the register that no regulation anywhere requires.

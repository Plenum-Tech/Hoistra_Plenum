# Concept: Hierarchy detection (Layer 3 relationship graph)

Recommended finalised structure: Locations -> Sites -> Assets -> Work Orders -> Resources -> Vendors.
Primary spine: organizations -> locations -> sites -> buildings -> floors -> spaces -> assets -> work_orders -> resources -> vendors.
Branches: compliance: assets -> compliance_certificates -> duty_holders; tenant_operations: sites -> spaces -> leases -> tenants; energy: assets -> meters -> meter_readings; spares: work_orders -> spare_parts -> manufacturers; contracts: vendors -> contracts -> resources; documents: document_chunks.
Edges are built from confirmed PK/FK pairs (see relationships.json) and stored in udr_relationship (provenance=schema_defined). Reference tables: asset_types, manufacturers, contracts.

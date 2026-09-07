# Canonical entity: Vendor (table `vendors`)

- Hierarchy level: vendor
- Primary key: `id`
- Description: Contractor / supplier organisation.
- Table aliases / synonyms: contractor, supplier, service provider, company, subcontractor
- CMMS/CAFM table synonyms: Maximo:COMPANIES(@0.95); SAP PM:LFA1(@0.95); Planon:BusinessPartner(@0.92)

## Columns
- `id` · type=uuid · class=primary_key
- `vendor_code` · type=string · class=standard · samples=VEN-007 · aliases=vendor id, vendor_id, supplier code
- `vendor_name` · type=string · class=standard · samples=Apex Lifts, TechCool · aliases=supplier name, contractor name
- `postcode` · type=string · class=shared_attribute · samples=AB1 2CD · aliases=post code, zip

## Relationships
- vendors.id —assigned_to(1:N)→ work_orders.vendor_id
- vendors.id —employs(1:N)→ resources.vendor_id
- vendors.id —party_to(1:N)→ contracts.vendor_id

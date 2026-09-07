# Canonical entity: Asset (table `assets`)

- Hierarchy level: asset
- Primary key: `id`
- Description: A maintainable piece of equipment (HVAC, lift, pump, generator, etc.).
- Table aliases / synonyms: equipment, plant, equip, machine, asset register, fixed asset
- CMMS/CAFM table synonyms: Maximo:ASSET(@0.98); Fiix:assets(@0.99); SAP PM:EQUI(@0.97); Archibus:eq(@0.95); Planon:Asset(@0.98); Concept Evolution:Equipment(@0.97)

## Columns
- `id` · type=uuid · class=primary_key
- `asset_code` · type=string · class=standard · samples=MOB-AHU-001, AST-4471 · aliases=asset id, asset_id, equipment number, equip#, equipment_id, tag, asset tag, asset ref · cmms=Maximo:ASSETNUM; Fiix:asset_id; SAP PM:EQUNR; Archibus:eq_id
- `asset_name` · type=string · class=standard · samples=AHU North Wing · aliases=equipment name, asset description, description
- `site_id` · type=uuid · class=foreign_key · -> sites · aliases=site code, site_code, location, building
- `asset_type_id` · type=uuid · class=foreign_key · -> asset_types · aliases=asset type, category, equipment type, asset class
- `manufacturer_id` · type=uuid · class=foreign_key · -> manufacturers · aliases=make, asset_make, manufacturer, brand, oem · cmms=SAP PM:HERST
- `serial_number` · type=string · class=shared_attribute · samples=SN-99213 · aliases=serial, serial no
- `installation_date` · type=date · class=standard · samples=2021-06-01 · aliases=install_date, install date, commission date · cmms=generic:install_date

## Relationships
- sites.id —contains(1:N)→ assets.site_id
- asset_types.id —classifies(1:N)→ assets.asset_type_id
- manufacturers.id —made_by(1:N)→ assets.manufacturer_id
- assets.id —raised_against(1:N)→ work_orders.asset_id
- assets.id —certified_by(1:N)→ compliance_certificates.asset_id
- assets.id —metered_by(1:N)→ meters.asset_id

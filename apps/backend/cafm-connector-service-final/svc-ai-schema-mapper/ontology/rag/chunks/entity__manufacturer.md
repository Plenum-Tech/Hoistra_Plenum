# Canonical entity: Manufacturer (table `manufacturers`)

- Hierarchy level: reference
- Primary key: `id`  · REFERENCE TABLE
- Description: Reference table for equipment makes. Canonical example of the shared-attribute -> reference-table promotion (asset_make 'Siemens 012').
- Table aliases / synonyms: make, oem, brand, asset_make

## Columns
- `id` · type=uuid · class=primary_key
- `manufacturer_name` · type=string · class=primary_key · samples=Siemens 012, Bosch Series C, Honeywell Env-3 · aliases=make, manufacturer, asset_make · cmms=generic:asset_make
- `manufacturer_country` · type=string · class=standard · samples=Germany
- `support_email` · type=string · class=standard · samples=support@siemens.com

## Relationships
- manufacturers.id —made_by(1:N)→ assets.manufacturer_id
- manufacturers.id —made_by(1:N)→ spare_parts.manufacturer_id

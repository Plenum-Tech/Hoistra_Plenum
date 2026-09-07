# Canonical entity: Asset Type (table `asset_types`)

- Hierarchy level: reference
- Primary key: `id`  · REFERENCE TABLE
- Description: Reference table for equipment classes. Auto-created when an asset 'type/category' is only a shared attribute (Test-1/Test-2 remediation).
- Table aliases / synonyms: asset category, equipment class, asset class

## Columns
- `id` · type=uuid · class=primary_key
- `asset_type` · type=string · class=primary_key · samples=Air Handler, Chiller, Lift, Generator, Pump · aliases=type, category

## Relationships
- asset_types.id —classifies(1:N)→ assets.asset_type_id

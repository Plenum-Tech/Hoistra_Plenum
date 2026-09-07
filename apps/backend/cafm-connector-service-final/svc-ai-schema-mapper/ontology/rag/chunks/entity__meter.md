# Canonical entity: Meter (table `meters`)

- Hierarchy level: energy
- Primary key: `id`
- Description: Energy / utility meter attached to an asset or site.
- Table aliases / synonyms: utility meter, submeter, energy meter

## Columns
- `id` · type=uuid · class=primary_key
- `meter_code` · type=string · class=standard · samples=MTR-ELEC-01 · aliases=meter id, meter ref
- `meter_type` · type=string · class=standard · samples=electricity, water, gas, btu
- `asset_id` · type=uuid · class=foreign_key · -> assets

## Relationships
- assets.id —metered_by(1:N)→ meters.asset_id
- meters.id —records(1:N)→ meter_readings.meter_id

# Canonical entity: Meter Reading (table `meter_readings`)

- Hierarchy level: energy
- Primary key: `id`
- Description: A reading captured from a meter.
- Table aliases / synonyms: reading, consumption, utility reading

## Columns
- `id` · type=uuid · class=primary_key
- `meter_id` · type=uuid · class=foreign_key · -> meters
- `reading_value` · type=number · class=standard · samples=10421.5 · aliases=value, consumption, kwh
- `reading_date` · type=datetime · class=standard · samples=2024-02-01T00:00:00

## Relationships
- meters.id —records(1:N)→ meter_readings.meter_id

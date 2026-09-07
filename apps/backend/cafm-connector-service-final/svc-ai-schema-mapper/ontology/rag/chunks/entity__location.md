# Canonical entity: Location (table `locations`)

- Hierarchy level: location
- Primary key: `id`
- Description: Geographic / portfolio grouping that contains sites.
- Table aliases / synonyms: region, portfolio, area, cluster

## Columns
- `id` · type=uuid · class=primary_key
- `location_code` · type=string · class=standard · samples=LOC-001, NORTH · aliases=region code, portfolio code
- `location_name` · type=string · class=standard · samples=North Region, London Portfolio

## Relationships
- locations.id —contains(1:N)→ sites.location_id

# Canonical entity: Space (table `spaces`)

- Hierarchy level: space
- Primary key: `id`
- Description: A tenant-occupiable unit / area within a site or building.
- Table aliases / synonyms: unit, room, area, suite, lettable unit
- CMMS/CAFM table synonyms: Archibus:rm(@0.93); MRI:Unit(@0.94)

## Columns
- `id` · type=uuid · class=primary_key
- `space_code` · type=string · class=standard · samples=U-101 · aliases=unit code, room number
- `space_type` · type=string · class=standard · samples=unit, common, plant_room, retail
- `area_sqm` · type=number · class=standard · samples=85.5

## Relationships
- sites.id —contains(1:N)→ spaces.site_id
- spaces.id —leased_under(1:N)→ leases.space_id

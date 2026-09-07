# Canonical entity: Site (table `sites`)

- Hierarchy level: site
- Primary key: `id`
- Description: A physical site/property under a location.
- Table aliases / synonyms: property, facility, building site, premises, estate
- CMMS/CAFM table synonyms: Maximo:LOCATIONS(@0.9); SAP PM:IFLOT(@0.88); Planon:Property(@0.93); MRI:Building(@0.92); Yardi:Property(@0.93)

## Columns
- `id` · type=uuid · class=primary_key
- `site_code` · type=string · class=standard · samples=SITE-0201, C0201 · aliases=property code, facility code, site ref, site_ref, site_id
- `site_name` · type=string · class=standard · samples=OfficeCampus 001, Tower 002 · aliases=property name, building name
- `city` · type=string · class=standard · samples=Sharjah, Ajman, Abu Dhabi
- `postcode` · type=string · class=shared_attribute · samples=AB1 2CD · aliases=post code, zip, zip code
- `country` · type=string · class=standard · samples=UAE
- `gfa_sqm` · type=number · class=standard · samples=10158, 63140 · aliases=gfa, gross floor area, area sqm
- `handover_date` · type=date · class=standard · samples=2024-03-15 · aliases=handover, ho date, completion date

## Relationships
- locations.id —contains(1:N)→ sites.location_id
- sites.id —contains(1:N)→ buildings.site_id
- sites.id —contains(1:N)→ spaces.site_id
- sites.id —contains(1:N)→ assets.site_id
- sites.id —located_at(1:N)→ work_orders.site_id

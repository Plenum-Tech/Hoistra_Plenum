# Canonical entity: Lease (table `leases`)

- Hierarchy level: tenant
- Primary key: `id`
- Description: A lease agreement linking a tenant to a space.
- Table aliases / synonyms: tenancy, rental agreement, occupancy agreement
- CMMS/CAFM table synonyms: Yardi:Lease(@0.97)

## Columns
- `id` · type=uuid · class=primary_key
- `lease_ref` · type=string · class=standard · samples=LSE-2023-12
- `tenant_id` · type=uuid · class=foreign_key · -> tenants
- `space_id` · type=uuid · class=foreign_key · -> spaces
- `rent_amount` · type=number · class=standard · samples=120000

## Relationships
- tenants.id —holds(1:N)→ leases.tenant_id
- spaces.id —leased_under(1:N)→ leases.space_id

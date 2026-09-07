# Canonical entity: Tenant (table `tenants`)

- Hierarchy level: tenant
- Primary key: `id`
- Description: An occupier / tenant of one or more spaces.
- Table aliases / synonyms: occupier, lessee, customer
- CMMS/CAFM table synonyms: MRI:Tenant(@0.97); Yardi:Tenant(@0.97)

## Columns
- `id` · type=uuid · class=primary_key
- `tenant_code` · type=string · class=standard · samples=TEN-44
- `tenant_name` · type=string · class=standard · samples=Acme Retail Ltd · aliases=occupier name, lessee name

## Relationships
- tenants.id —holds(1:N)→ leases.tenant_id

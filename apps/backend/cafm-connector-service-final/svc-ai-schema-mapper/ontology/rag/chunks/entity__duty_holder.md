# Canonical entity: Duty Holder (table `duty_holders`)

- Hierarchy level: compliance
- Primary key: `id`
- Description: Person accountable for a compliance obligation (Responsible Person).
- Table aliases / synonyms: responsible person, accountable person, rp

## Columns
- `id` · type=uuid · class=primary_key
- `name` · type=string · class=standard · samples=Khalid Al Rashid
- `role` · type=string · class=standard · samples=Responsible Person

## Relationships
- duty_holders.id —responsible_for(1:N)→ compliance_certificates.duty_holder_id

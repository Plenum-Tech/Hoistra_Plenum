# Canonical entity: Resource (table `resources`)

- Hierarchy level: resource
- Primary key: `id`
- Description: Labour / manpower / skill allocated to work orders.
- Table aliases / synonyms: labour, manpower, technician, craft, operative, engineer, wo_labour
- CMMS/CAFM table synonyms: Maximo:LABTRANS(@0.92)

## Columns
- `id` · type=uuid · class=primary_key
- `resource_code` · type=string · class=standard · samples=RES-12 · aliases=labour id, craft_code, employee id
- `skill_type` · type=string · class=standard · samples=Electrical, Mechanical, HVAC · aliases=craft, craft_code, trade, discipline · cmms=generic:craft_code
- `vendor_id` · type=uuid · class=foreign_key · -> vendors
- `contract_id` · type=uuid · class=foreign_key · -> contracts · aliases=contract_id, contract ref

## Relationships
- resources.id —allocated_to(1:N)→ work_order_resources.resource_id
- vendors.id —employs(1:N)→ resources.vendor_id
- contracts.id —governs(1:N)→ resources.contract_id

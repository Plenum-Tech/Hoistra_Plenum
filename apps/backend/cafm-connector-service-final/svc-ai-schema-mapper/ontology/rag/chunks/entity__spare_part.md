# Canonical entity: Spare Part (table `spare_parts`)

- Hierarchy level: spares
- Primary key: `id`
- Description: Inventory item / spare part used on work orders.
- Table aliases / synonyms: part, material, item, stock, inventory, wo_parts, spares
- CMMS/CAFM table synonyms: Maximo:INVENTORY(@0.94); Fiix:parts(@0.96)

## Columns
- `id` · type=uuid · class=primary_key
- `part_number` · type=string · class=standard · samples=MOTOR-8HP, GEN-SVC-001 · aliases=part_code, item_ref, item code, material number, sku · cmms=generic:item_ref
- `part_name` · type=string · class=standard · samples=8HP Motor · aliases=item description, material description
- `manufacturer_id` · type=uuid · class=foreign_key · -> manufacturers

## Relationships
- manufacturers.id —made_by(1:N)→ spare_parts.manufacturer_id

# Canonical entity: Contract (table `contracts`)

- Hierarchy level: reference
- Primary key: `id`  · REFERENCE TABLE
- Description: Reference table for vendor contracts. Auto-created when contract_id overlaps across tables without a reference table (Test 2 example).
- Table aliases / synonyms: agreement, sla, contract ref
- CMMS/CAFM table synonyms: Fiix:purchase_orders(@0.8)

## Columns
- `id` · type=uuid · class=primary_key
- `contract_ref` · type=string · class=primary_key · samples=CON-2024-11 · aliases=contract_id, contract ref, agreement no
- `vendor_id` · type=uuid · class=foreign_key · -> vendors

## Relationships
- vendors.id —party_to(1:N)→ contracts.vendor_id
- contracts.id —governs(1:N)→ resources.contract_id

# Canonical entity: Work Order (table `work_orders`)

- Hierarchy level: work_order
- Primary key: `id`
- Description: A maintenance job raised against an asset.
- Table aliases / synonyms: wo, job, ticket, task, service request, maintenance order, order, work request, work log, work_tasks
- CMMS/CAFM table synonyms: Maximo:WORKORDER(@0.98); Fiix:work_orders(@0.99); SAP PM:AUFK(@0.93); SAP PM:AFIH(@0.92); Archibus:wr(@0.95); Planon:Order(@0.96); Concept Evolution:Task(@0.93)

## Columns
- `id` · type=uuid · class=primary_key
- `workorder_ref` · type=string · class=standard · samples=WO-0001, WONUM-55231 · aliases=wo number, wo_number, wonum, work order id, wo_id, job number, ticket id · cmms=Maximo:WONUM; Fiix:wo_number; Archibus:wr_id
- `asset_id` · type=uuid · class=foreign_key · -> assets · aliases=asset code, asset_id, equipment
- `site_id` · type=uuid · class=foreign_key · -> sites · aliases=site ref, site_ref, site_code · cmms=Maximo:SITEID; generic:site_ref
- `vendor_id` · type=uuid · class=foreign_key · -> vendors · aliases=contractor, supplier
- `priority` · type=string · class=standard · samples=P1, P2, URGENT, High · aliases=wo_priority, priority_level, severity · cmms=Maximo:WOPRIORITY; Fiix:wo_priority
- `status` · type=string · class=standard · samples=Open, Closed, In Progress · aliases=wo_status, state
- `work_type` · type=string · class=standard · samples=reactive, planned, ppm · aliases=wo_type, maintenance_type, job type
- `raised_date` · type=date · class=standard · samples=2024-01-15 · aliases=created date, open date, reported date

## Relationships
- assets.id —raised_against(1:N)→ work_orders.asset_id
- sites.id —located_at(1:N)→ work_orders.site_id
- vendors.id —assigned_to(1:N)→ work_orders.vendor_id
- work_orders.id —uses(1:N)→ work_order_resources.work_order_id

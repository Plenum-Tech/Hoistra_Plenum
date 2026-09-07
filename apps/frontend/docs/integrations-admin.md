# Hoistra skill — Integrations (Admin)

> One of the Hoistra platform skill files. Start with `skills/platform-overview.md` for the product model, principles and file map; each skill file covers one area of `Hoistra.dc.html` and its data files.

## Integrations (Admin)
A saved report over the connection tables; admin only.

- **Header tiles:** Connected · Tables fed · Need attention · Available.
- **Connected** — table: connection, status (Healthy / Degraded / Auth expired / Rate limited / In review), last sync, rows 30d, tables fed, connected by. Expand a row for: mode and auth; **tables it enriches** (orange) vs **tables that exist only because of it** (marker yellow); what those tables unlock; a note; actions (Re-authorise / Resync / Field mapping / Open in the graph / Disconnect). Six seeded: Yardi Voyager, SAP S/4HANA, IBM Maximo (key expired, 0 rows — staleness shown, not hidden), Planon Universe, Reuters Connect, custom data lake.
- **Available sources** — search + category chips; 58 connectors in nine categories, each card naming the graph tables it would write: Finance and property accounting (Yardi, MRI, RealPage, AppFolio, Entrata, Sage Intacct, Coupa, AvidXchange) · ERP — Oracle · ERP — Microsoft · ERP — SAP · CMMS and CAFM · IWMS · Asset, plant and BMS · News and market intelligence · Custom and direct. **Connect** opens a modal (name, instance URL, target tables); the mapping is held in the decision queue until approved.
- **Custom API** — base URL, maskable bearer token, rate limits, five endpoints, field-mapping table (source field → table.column, natural key marked).

Data in `hoistra-integrations.js`.


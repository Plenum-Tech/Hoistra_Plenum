# Hoistra skill — Compliance console

> One of the Hoistra platform skill files. Start with `skills/platform-overview.md` for the product model, principles and file map; each skill file covers one area of `Hoistra.dc.html` and its data files.

## Compliance console

Scope cascade **country → state → building**, then:
- **Needs you** risk cards (lapsed, authenticity failed, inside 30 days, inside 90 days, building certificates, vendor certificates, all certificates, portfolio coverage) — each readable in **building view** or **vendor view** and routing to a different action.
- **Expiry runway** — every certificate in scope as a marker on a time axis with the 30/90-day bands.
- **Building and vendor pivots** with a focus record: building vault, vendor impact, not on record.
- **Route 2:** scope to a building → click a certificate → action → orchestrator.

Data in `hoistra-compliance.js`.


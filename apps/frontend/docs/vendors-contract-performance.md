# Hoistra skill — Vendors — contract performance

> One of the Hoistra platform skill files. Start with `skills/platform-overview.md` for the product model, principles and file map; each skill file covers one area of `Hoistra.dc.html` and its data files.

## Vendors — Contract performance

- **Insights and actions** (six tiles): vendors blocked, pending critical, L1 breaches, invoice lines held, terms on default, plus a routed tile each — every tile opens its data.
- **Stats** (four cards): vendors by accreditation status, packages with single-point-of-failure flags, contracts by source coverage, commercial orders.
- **Vendor directory** with score, trend, coverage bar, and an accreditation cap line where one applies.
- **Scorecard** for the selected vendor. Every metric shows *contract requirement (clause) · measured · sample size*. Score is **capped by accreditation status** (Meridian 42/100, ceiling 60). Tabs:
  - **Evidence** — every breach with its weight multiplier (L1 = 3×) and the service credit it earns, summed as recoverable this period.
  - **Coverage** — which of the contract's terms were parsed (Apex 9 of 12, Meridian 4 of 18); platform defaults in marker yellow.
  - **Invoices** — lines checked against the rate schedule, mismatches flagged.
  - **Terms** — each term with source (clause and page, or *default*).

Data in `hoistra-vendors.js`.


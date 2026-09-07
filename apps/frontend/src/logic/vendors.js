// vendors — vendor record drawer.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { t } from './constants.js';

export const vendorsMethods = {
  vendorDetail(v) {
    return {
      module: "Vendor performance", icon: "ph-chart-line-up", tone: v.tone,
      title: v.name + " — score " + v.score + ", " + v.trend,
      meta: "Annual spend " + v.spend + " · accreditation " + v.accred + " · scored on 312 completed work orders",
      body: (v.note || "No exceptions raised this period.") + " SLA response is at " + v.sla_r + "% and completion at " + v.sla_c + "% against a 95% contracted target. First-fix rate " + v.firstfix + "%, recall rate " + v.recall + "%.",
      fields: [
        { l: "Overall score", v: v.score + " / 100 · " + v.trend },
        { l: "SLA response met", v: v.sla_r + "% (weight 25%)" },
        { l: "SLA completion met", v: v.sla_c + "% (weight 25%)" },
        { l: "First fix rate", v: v.firstfix + "% (weight 20%)" },
        { l: "Recall rate", v: v.recall + "% (weight 15%)" },
        { l: "Accreditation", v: v.accred + " (weight 15%)" },
        { l: "Contract review date", v: "October 2026", editable: true }
      ],
      chain: [
        { a: "Orchestrator", t: "Intent: contract-performance-engine. Period resolved to August 2026." },
        { a: "Planner", t: "Fetch completed WOs for vendor → score against extracted SLA baseline → weight by asset criticality → aggregate" },
        { a: "Worker", t: "Work orders scored. L1 asset failures weighted 3×. Cost variance run against pre-job estimates." },
        { a: "Quality", t: v.tone === "risk" ? "Fired — score movement above 15 points checked for data anomaly before presenting. Confirmed genuine." : "Not fired — read-only scorecard output." }
      ],
      refinement: "Model the effect of moving 30% of " + v.name + "'s volume to your highest-scoring vendor in the same trade?",
      actions: v.accred === "Lapsed" ? ["Reassign work orders", "Request evidence", "Flag for contract review"] : ["Raise service credit note", "Open contract", "Flag for review"]
    };
  }
};

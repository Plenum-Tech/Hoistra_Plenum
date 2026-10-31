// platformNotices — what the platform itself needs from you, for the Notifications drawer.
//
// Notifications held what the records need: approvals, energy anomalies, maintenance
// decisions. Aasim (8 Oct 2026): they should also carry the platform's own actions — a low
// Hoist Score that ingesting would raise, a scheduled check that has stopped working, a check
// you could have scheduled and have not, and what the platform is costing. Each notice opens
// the place that acts on it. Pure: the controller hands in what it has already read.
//
//   score       homeLive's Hoist Score ({value, answered, band, gap})
//   cronJobs    the company's Hoist Crons (null until read)
//   me          the signed-in email, to tell your own crons from the company's
//   usage       GET /api/admin/usage totals (admins only; null otherwise)
//
// `info: true` marks a notice that is worth knowing but waits on nobody (a suggestion, a
// figure): it is listed under Platform and not counted in the "N Pending" badge.

// Below this the Hoist Score is "Ingestion in progress" (homeLive bandOf): a progress figure,
// not yet an autonomy grade. Under 40 it is most of the portfolio missing.
export const SCORE_LOW = 60;
const SCORE_RISK = 40;

import { scrubInternal } from './publicText.js';

const ICON = "ph-gauge";
const lower = (s) => String(s || "").trim().toLowerCase();

function agoText(iso, now) {
  const t = Date.parse(iso || "");
  if (isNaN(t)) return "";
  const min = Math.max(0, Math.round((now - t) / 60000));
  return min < 60 ? min + " min ago" : min < 1440 ? Math.round(min / 60) + " h ago" : Math.round(min / 1440) + " d ago";
}

export function platformNotices(input) {
  const o = input || {};
  const now = o.now instanceof Date ? o.now.getTime() : Date.now();
  const out = [];
  const add = (n) => out.push(Object.assign({ kind: "platform", module: "Platform", icon: ICON, moneyValue: null, at: 0, info: false }, n));

  // ── Hoist Score: low means the platform is working from part of your portfolio ──
  const sc = o.score || null;
  if (sc && typeof sc.value === "number" && sc.value < SCORE_LOW) {
    add({
      key: "platform:score",
      tone: sc.value < SCORE_RISK ? "risk" : "warn",
      title: "Hoist Score is " + sc.value + "/100 — ingest what is missing to raise it",
      meta: sc.gap || "Some of your records are not on Hoistra yet.",
      money: sc.value + "/100",
      // Someone who cannot ingest is shown the buildings and what each is missing, not an
      // upload card they would be refused at.
      action: o.canIngest ? { type: "ingest" } : { type: "buildings" }
    });
  } else if (sc && sc.answered && sc.value === null && sc.band === "Nothing hoisted") {
    add({
      key: "platform:score",
      tone: "warn",
      title: "No buildings hoisted yet — hoist one to start your Hoist Score",
      meta: "Every answer, check and score is worked out per building.",
      money: "Not started",
      action: { type: "buildings" }
    });
  }

  // ── Hoist Crons: a check that stopped working is a check nobody is doing ──
  const jobs = Array.isArray(o.cronJobs) ? o.cronJobs : null;
  (jobs || []).forEach((j) => {
    const last = (j && j.runs && j.runs[0]) || null;
    if (!j || !j.enabled || !last || last.ok) return;
    add({
      key: "platform:cron:" + j.id,
      tone: "risk",
      title: (j.name || "A Hoist Cron") + " failed its last run",
      meta: "Hoist Cron · " + (agoText(last.finished_at, now) || "last run") + (last.error ? ": " + scrubInternal(String(last.error)).slice(0, 140) : ""),
      money: "Failed",
      action: { type: "cron", id: j.id }
    });
  });

  // ── Nothing scheduled for you: the daily check you could have and do not ──
  const me = lower(o.me);
  if (jobs && o.canManageCrons && me && !jobs.some((j) => lower((j.created_by && j.created_by.email) || j.owner_email) === me)) {
    add({
      key: "platform:schedule",
      tone: "info",
      info: true,
      title: "Nothing is scheduled for you — schedule what you check every day",
      meta: "A Hoist Cron runs a check on its own and shows what it found on Home — a daily compliance expiry scan, a weekly SLA watch.",
      money: "Suggestion",
      action: { type: "schedule" }
    });
  }

  // ── What the platform is costing: credits this month (admins) ──
  const u = o.isAdmin ? o.usage || null : null;
  const credits = u && typeof u.credits_this_month === "number" ? u.credits_this_month : null;
  if (credits !== null && credits > 0) {
    const n = Math.round(credits).toLocaleString("en-GB");
    add({
      key: "platform:usage",
      tone: "info",
      info: true,
      title: "Platform usage this month: " + n + " credits",
      meta: "A question is 1 credit, an ingest 5. Who used them is under Users & access.",
      money: n + " credits",
      action: { type: "usage" }
    });
  }
  return out;
}

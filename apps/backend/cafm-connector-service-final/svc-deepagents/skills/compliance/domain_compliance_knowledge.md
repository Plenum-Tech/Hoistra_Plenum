---
name: compliance-domain-knowledge
agent: compliance
title: How a compliance professional weighs risk
description: ALWAYS loaded by the analyst and the reviewer. What each certificate type protects, how a lapse or a forged document translates into risk, and how to rank buildings and vendors when a question says "risk", "worst", "most urgent" or "priority".
---
You are answering for a UK property manager who is personally accountable for the safety of the
people in these buildings. "Risk" is not a count of expired rows and it is not days overdue. It
is **what can happen to whom if this duty is not evidenced** — consequence first, then how likely
and how long the exposure has run. Rank on that, and say so in the answer.

## 1. Consequence tiers — what each duty protects

Rank by the tier of the certificate type before anything else. A lapsed document in Tier 1 outranks
any document in Tier 2 or 3, however long the latter has been overdue.

**Tier 1 — life safety (people can be killed or seriously hurt when this is not in place).**
- Fire: Fire Risk Assessment (FRA), Fire Alarm System Service, Emergency Lighting, Fire Door
  Inspection, Sprinkler / Suppression test. The FRA is the keystone — without a current FRA the
  responsible person cannot show they know the building's fire risks at all (Regulatory Reform
  (Fire Safety) Order 2005; a criminal offence for the responsible person).
- Gas: Gas Safety Certificate (CP17), Boiler Service Record — carbon monoxide and explosion risk.
- Electrical: EICR, EIC — fire and electrocution risk; the EICR is the only evidence the fixed
  installation is safe.
- Lifts and pressure: LOLER Thorough Examination, Written Scheme of Examination / pressure vessel
  inspection — mechanical failure with people inside or nearby.
- Asbestos: Management Survey, Asbestos Register — exposure of occupants and contractors.
- Water: Legionella Risk Assessment, Legionella Monitoring, Cold Water Tank inspection —
  Legionnaires' disease.
- Building Safety Act (higher-risk buildings only): Safety Case Report, HRB Registration — the
  post-Grenfell regime; where it applies, it is Tier 1.
- Vendor side: Gas Safe registration and ACS card (unregistered gas work is a criminal offence and
  a CO risk), NICEIC/NAPIT and City & Guilds 2382, HSE Asbestos Licence and P402/3/4, BAFE fire
  schemes, LOLER competent person / LEIA, LCA / legionella competency, REFCOM / F-Gas technician
  where refrigerant is handled. An unaccredited contractor doing this work is the same hazard as
  the missing building certificate.

**Tier 2 — statutory and legal exposure without an immediate physical hazard.**
- Employers' Liability Insurance (compulsory; fines per day uninsured; injured staff uncompensated),
  Health & Safety Policy Statement, General Risk Assessment Register.
- Vendor side: Contractors' Employers' and Public Liability insurance, CHAS / SSIP, SIA licensing
  (a legal requirement for guarding; safety consequence indirect), ISO 9001 / 14001 (commercial,
  not statutory).

**Tier 3 — energy, environmental and performance duties.**
- EPC, DEC, TM44 air-conditioning inspection, ESOS. Statutory, fines apply, but nobody is hurt
  when they lapse. F-Gas leak checks sit between Tier 3 and Tier 1 (environmental duty; refrigerant
  leaks are rarely a direct injury risk in occupied space).
- Vendor side: BPCA / pesticide certificates are Tier 2 for occupant health; ISO certificates
  Tier 3.

## 2. What a forged or suspect document means

A certificate with `forensics_verdict = fail` (edited) does not evidence the duty at all — treat
the duty as **uncovered**, exactly as if nothing were on file — and it adds a second problem: a
document someone altered or fabricated, which is a governance and possible fraud matter that must
be escalated regardless of the certificate's date. A forged Tier 1 document is therefore worse
than a genuinely lapsed one of the same type: the building has no valid evidence *and* someone
tried to make it look as though it had. `review / suspect` is a flag to verify, not a finding of
forgery; say "suspect", not "forged".

## 3. How to rank when the question says risk, worst, most urgent, highest

Apply in this order and name the order you used:
1. **Consequence tier of the lapsed or missing type** (Tier 1 before 2 before 3).
2. **Authenticity** — a failed forensics verdict on a Tier 1 document lifts it above a genuine
   lapse in the same tier.
3. **Duration and count** — how long the exposure has run, and how many duties are lapsed.
4. **Applicability and occupancy** where the data gives it — a lapsed FRA in an occupied building
   outranks one in a vacant one; a lift duty only bites where there is a lift.

Days overdue is a tie-breaker inside a tier, never the headline. Twenty years without a fire
risk assessment is severe *because it is a fire risk assessment*, not because of the number.

Worked example from this register: four buildings each hold one lapsed certificate. AN Other
House's Fire Risk Assessment (Tier 1, fire, occupants' lives, lapsed since 2006) ranks first.
Building 5's EICR (Tier 1, electrical) ranks second and carries a second, separate problem — the
document failed forensics (100/100, edited), so the electrical installation has no valid
evidence and the file holds an altered document. Bishopsgate Tower's Employers' Liability
Insurance (Tier 2, suspect authenticity) ranks third. Town Hall's Display Energy Certificate
(Tier 3, genuine) ranks last, despite being lapsed for thirteen years. Say all of that in one
paragraph; give only the first — and the second where its reason is distinct, as here — a group.

## 4. What "expired" and "coverage" mean for risk

- Lapsed / expired means the duty is currently unevidenced; the hazard it controls is
  unmanaged as far as anyone can prove. Say what the hazard is.
- A type with nothing on record is the same exposure as a lapsed one, with less history.
- Coverage (N of 27 pack types on file) measures pack completeness, not risk. Do not rank
  buildings on coverage when the question is about risk; use it only as supporting context.
- Unlinked rows (no building) cannot be ranked against buildings; say so in one sentence and
  count them, do not give them a group in a which-building answer.

## 5. Language a property manager expects

Name the regulation and who is exposed: "the Fire Risk Assessment under the Regulatory Reform
(Fire Safety) Order 2005 has been lapsed since 2006 — the responsible person cannot show the
fire risks to occupants are known or controlled." Prefer "life-safety", "statutory", "energy
duty" to abstract severity words. Say what to do first and why it is first.

"""Synthetic question batches, one role space at a time.

An evaluation batch has to be asked somewhere. A question asked in Ops Brain cannot reach the
compliance engine (svc-deepagents agents/spaces.py), so "50-100 synthetic questions" is not one
set: it is a set per space, and a batch generated without knowing its space will contain
questions the space refuses. A refusal is a correct answer there, which makes the batch measure
the gate rather than the engines, and nothing in the result says which happened.

So generation starts from the space. Each space names the domain engines it may use; each engine
has questions a facilities manager actually asks of it; every generated question is then CHECKED
against the real router before it is handed out, and one that would route outside its own space
is dropped rather than shipped. That check is the point of this module -- without it the
generator is a list of strings somebody has to read.

Pure: no database, no model, no network. Seeded, so a batch is reproducible and a failure can be
re-run exactly. The dimensions follow docs/superpowers/skills/scenario-generation/SKILL.md --
role, goal, difficulty, writing style -- in the small: that skill describes a 250-scenario batch
with grounding in real rows, which needs a database and a person's review pass. This is the part
that can be generated and verified with neither.

G0: imports svc-deepagents, changes nothing in it.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from typing import Any, Iterable

#: Every space a batch can be generated for. "maintenance" is not a space of its own -- planned
#: and reactive maintenance is the work-order engine, which Ops Brain and the Assets Operating
#: System both have; ask for "ops" or "assets".
SPACES = ("ops", "comp", "assets", "full")

#: Questions a facilities manager asks of each domain engine, as they would actually type them.
#: Grouped by the engine that should answer, which is what makes the space check below possible.
BY_ENGINE: dict[str, tuple[str, ...]] = {
    "wo_engine": (
        "what is open across my buildings this week",
        "is planned maintenance running to plan",
        "which work orders breached sla",
        "list overdue work orders",
        "show the ppm deviations",
        "which inspection recommendations were never actioned",
        "what needs my approval today",
        "how many jobs did we close last month",
        "which building raises the most reactive work",
        "what is still open from last quarter",
    # Added 3 Nov 2026 because a batch of 25 for Ops came back with 9. A batch never asks the
    # same question twice, so it is capped by how many DISTINCT questions route to the instance,
    # and 13 of the 32 written before this route NOWHERE at all. Every question below was put
    # through the real router before it was added and kept only if it reached this engine --
    # which is why the contract ones are worded as they are. "how much have we spent against
    # each contract this year" routes nowhere: the router's vocabulary for this engine is sla,
    # contractor, scorecard, first fix, invoice, day rate, ppm compliance and the rest, and not
    # the bare word "contract".
        "which work orders are overdue right now",
        "how many work orders are open at each building",
        "which work orders have been reopened",
        "what is the average time to close a work order",
        "which work orders are waiting on a part",
        "how many work orders did each vendor complete",
        "which planned maintenance tasks were missed last month",
        "what maintenance is due in the next 30 days",
        "which work orders have no vendor assigned",
        "which assets raise the most work orders",
        "how long are work orders sitting before anyone attends",
        "which work orders were closed without a completion note",
        "which work orders breached their fix sla",
        "how much reactive maintenance did we do last month",
        "which buildings have the oldest open work orders",
    # Added 10 Nov 2026: a batch of 50-100 (30 Oct feedback, item 7) came back short -- Compliance
    # 44 of 100, Ops 62, Assets 84 -- because a batch never asks one question twice and these
    # engines had too few. Each was put through the real router and kept only if it reached this
    # engine, as above.
        "which work orders have been open longer than 30 days",
        "how many reactive repairs did each building raise this month",
        "what is the maintenance backlog at each building",
        "which technician closed the most work orders last week",
        "who maintains the chillers at each building",
        "which assets have had repeat breakdowns",
        "when was each boiler last serviced",
        "which repairs are waiting for my decision",
        "what planned maintenance is scheduled for next week",
        "which assets are near end of life",
        "what is the service history of the main air handling unit",
        "how many work orders were raised out of hours",
    ),
    "contract_performance": (
        "which vendor is costing me money",
        "is each vendor inside its sla",
        "what service credit can i claim",
        "which vendors are blocked right now",
        "who can replace a blocked vendor",
        "which contracts come up for renewal",
        "what did we spend with each contractor this year",
        "which vendor misses its response times most",
    # Added 3 Nov 2026 because a batch of 25 for Ops came back with 9. A batch never asks the
    # same question twice, so it is capped by how many DISTINCT questions route to the instance,
    # and 13 of the 32 written before this route NOWHERE at all. Every question below was put
    # through the real router before it was added and kept only if it reached this engine --
    # which is why the contract ones are worded as they are. "how much have we spent against
    # each contract this year" routes nowhere: the router's vocabulary for this engine is sla,
    # contractor, scorecard, first fix, invoice, day rate, ppm compliance and the rest, and not
    # the bare word "contract".
        "which contractor has the worst sla performance",
        "show me each vendor scorecard for last month",
        "which contractor is overcharging us",
        "what is each contractor first fix rate",
        "which vendors have a recall on recent work",
        "what is our ppm compliance by contractor",
        "which contractor overran on cost last quarter",
        "are any invoices above the capped day rate",
        "what is the labour rate we agreed with each contractor",
        "which vendor scorecard dropped the most this quarter",
        "how is each contractor performing against its kpi",
        "which contractor has the most sla breaches",
        "is anyone overbilling us on hourly rate",
        "what is the cost variance against contract terms this year",
        "which contractor is performing best on ppm compliance",
        "show the sla performance for every contractor this month",
        "which invoices look like overbilling",
        "what is the first fix rate by contractor and building",
        "which contractor had the most recalls last year",
        "has any contractor breached its contract terms",
    # Added 10 Nov 2026: a batch of 50-100 (30 Oct feedback, item 7) came back short -- Compliance
    # 44 of 100, Ops 62, Assets 84 -- because a batch never asks one question twice and these
    # engines had too few. Each was put through the real router and kept only if it reached this
    # engine, as above.
        "which contractor responds slowest to p1 sla calls",
        "what is the average sla response time per contractor",
        "how many sla breaches did we have last month",
        "which building has the most sla breaches this quarter",
        "which contractor improved its performance the most this year",
        "which contractor's performance is getting worse",
        "what is the kpi trend for each contractor over six months",
        "which vendor score fell below target last month",
        "which contractor has a falling scorecard trend",
        "show the scorecard trend for our hvac contractor",
        "what is the first fix rate across the portfolio",
        "which contractor has the lowest first fix rate",
        "how many recalls did each contractor have this quarter",
        "which jobs were a recall within 30 days",
        "which invoices are still unpaid from contractors",
        "which invoices do not match the agreed day rate",
        "how much did we pay above the capped day rate this year",
        "which contractor billed the highest hourly rate last month",
        "are we overpaying any contractor for call-outs",
        "which invoices were submitted twice",
        "what is the total invoice value per contractor this quarter",
        "which contractor overran the most on cost this year",
        "how much cost overrun did we have last quarter",
        "what is the cost variance by building this quarter",
        "which contractor is most over budget against contract terms",
        "which contractors are meeting their kpi targets",
        "which contractors missed their kpi targets last month",
        "how is our cleaning contractor performing",
        "how is the lift contractor performing this quarter",
        "which contractor is performing worst at our biggest building",
        "what is the ppm compliance rate for each building",
        "which contractor has the lowest ppm compliance this month",
        "has ppm compliance improved since last quarter",
        "what service credits are owed for sla breaches this month",
        "how much could we claim back for missed sla targets",
        "which contractor's labour rate went up this year",
        "are any labour rates above what the contract terms allow",
        "which contractor has the highest hourly rate for out of hours work",
        "compare contractor performance across our buildings",
        "rank our contractors by sla performance",
        "rank vendors by vendor score this quarter",
        "which contractor should we review at the next performance meeting",
        "which contractor is closest to a contract breach",
        "has anyone breached sla on a p1 emergency this week",
        "what percentage of jobs met the sla last month",
        "what is the average invoice per job for each contractor",
        "which contractor's invoices rose fastest this year",
        "are we being overcharged on materials by any contractor",
        "which contractor gives the best value for its performance",
        "show the kpi results for every contractor",
        "what is the sla attainment by priority",
        "which contractor has improved its first fix rate",
        "which contractor had a recall at the same asset twice",
        "how many invoices were disputed last quarter",
        "what is our total overrun against the agreed contract terms",
        "which contractor scorecard is below 70 percent",
        "how did contractor performance change after the new kpis",
        "which subcontractor is dragging down performance",
        "is our security contractor inside its sla",
        "which contractor has the best response sla record",
    ),
    "compliance": (
        "which buildings are out of compliance today",
        "what certificates lapse in the next 30 days",
        "which certificates failed authenticity checks",
        "which vendors are not eligible to work here",
        "what documents evidence each certificate",
        "which approvals expire this month",
        "when is the next loler inspection due",
        "show the eicr certificate for each building",
    # Added 3 Nov 2026 because a batch of 25 for Ops came back with 9. A batch never asks the
    # same question twice, so it is capped by how many DISTINCT questions route to the instance,
    # and 13 of the 32 written before this route NOWHERE at all. Every question below was put
    # through the real router before it was added and kept only if it reached this engine --
    # which is why the contract ones are worded as they are. "how much have we spent against
    # each contract this year" routes nowhere: the router's vocabulary for this engine is sla,
    # contractor, scorecard, first fix, invoice, day rate, ppm compliance and the rest, and not
    # the bare word "contract".
        "which certificates expire in the next 60 days",
        "what is our compliance rate by building",
        "which buildings have no fire risk assessment",
        "which statutory inspections are overdue",
        "what compliance documents are missing for each building",
        "which certificates were issued by an unverified body",
        "how many certificates lapsed last quarter",
    # Added 10 Nov 2026: a batch of 50-100 (30 Oct feedback, item 7) came back short -- Compliance
    # 44 of 100, Ops 62, Assets 84 -- because a batch never asks one question twice and these
    # engines had too few. Each was put through the real router and kept only if it reached this
    # engine, as above.
        "which gas safety certificates expire next month",
        "which buildings have an expired eicr",
        "when does each building's fire risk assessment need renewal",
        "which vendors have lapsed accreditations",
        "which vendor accreditation expires soonest",
        "is every gas engineer on the gas safe register",
        "which electricians are not niceic registered",
        "which lifts are overdue a loler inspection",
        "what remedial actions are open from the last eicr",
        "which certificates are not on record for each building",
        "what is our insurance risk from expired certificates",
        "which statutory inspections are due next quarter",
        "how many certificates expire this year",
        "which building has the most expired certificates",
        "list every certificate that expired last month",
        "which vendors are blocked because of lapsed insurance",
        "which buildings are missing a gas safety record",
        "what is the compliance status of each building",
        "which compliance items are overdue at our largest building",
        "which water hygiene inspections are overdue",
        "when is the next fire alarm inspection due",
        "which buildings have no asbestos certificate",
        "which vendor certs expire in the next 90 days",
        "which building certs are due for renewal this quarter",
        "what remedial works are outstanding from fire risk assessments",
        "which emergency lighting certificates have lapsed",
        "what is our statutory compliance percentage",
        "which certificate renewals are overdue",
        "which vendors are missing an accreditation for the work they do",
        "what compliance deadlines fall next month",
        "which pressure system inspections are due",
        "which buildings have expired legionella certificates",
        "show the expiry date of every lift certificate",
        "which accreditations were checked and failed",
        "which vendors cannot work on site until their certs are renewed",
    ),
    "energy_intelligence": (
        "which assets are losing value",
        "what is energy telling me about asset health",
        "which assets should i remediate first",
        "what will break in the next 90 days",
        "which building uses the most energy per square metre",
        "where is consumption rising fastest",
    # Added 3 Nov 2026 because a batch of 25 for Ops came back with 9. A batch never asks the
    # same question twice, so it is capped by how many DISTINCT questions route to the instance,
    # and 13 of the 32 written before this route NOWHERE at all. Every question below was put
    # through the real router before it was added and kept only if it reached this engine --
    # which is why the contract ones are worded as they are. "how much have we spent against
    # each contract this year" routes nowhere: the router's vocabulary for this engine is sla,
    # contractor, scorecard, first fix, invoice, day rate, ppm compliance and the rest, and not
    # the bare word "contract".
        "what did we spend on energy last month",
        "which buildings use the most energy overall",
        "how does energy use compare year on year",
        "which meters show an unexpected jump",
        "what is the energy intensity of each building",
        "which buildings have the worst energy rating",
    ),
}

#: Who is asking. From the skill's Role dimension.
ROLES = ("operative", "building manager", "vendor", "consultant", "superadmin")

#: Questions that need MORE THAN ONE area to answer, which is how a facilities manager actually
#: asks. "What is this work order's status, did it meet its response SLA, and how has that vendor
#: been performing here overall?" is one question and three lookups, and the thing worth measuring
#: is whether the answer carries all three parts and ties them together -- a reply that answers
#: the first and quietly drops the other two looks fine and is wrong.
#:
#: `needs` is the areas the question spans. It is offered only to an instance that has ALL of
#: them: asked anywhere else it would be a refusal, or worse, a half-answer that reads as a whole
#: one. `covers` is what a good answer has to address, which is what a reviewer judges against --
#: it is NOT an expected answer, which only a person reading real rows can write.
CROSS_DOMAIN: tuple[dict[str, Any], ...] = (
    {"question": "take the work order that last breached its response SLA - what is its status now, "
                 "did it meet the SLA, and how has the vendor who attended been performing at that "
                 "building overall",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the work order's current status; whether the response SLA was met; that vendor's "
               "performance at that building, not across the portfolio"},
    {"question": "which inspection recommendations are still open, and has anyone raised work orders "
                 "against them",
     "needs": ("compliance", "wo_engine"),
     "covers": "the open recommendations; whether each has work raised against it; the ones with "
               "nothing raised named explicitly"},
    {"question": "for the vendor with the most lapsed certificates, how are they performing against "
                 "their contract and are they still inside sla",
     "needs": ("compliance", "contract_performance"),
     "covers": "which vendor; what has lapsed; their contract performance and SLA position, with "
               "the two set against each other rather than listed"},
    # Worded to lead with energy on purpose: "where is consumption rising fastest, and what are we
    # paying the contractor who maintains the plant there" routes to the WORK-ORDER engine, which
    # owns no part of it. The router reads the opening, so a cross-domain question has to open on
    # an area it actually needs.
    {"question": "where is energy consumption rising fastest and what do we pay that building's "
                 "contractor",
     "needs": ("energy_intelligence", "contract_performance"),
     "covers": "where consumption is rising; the contractor responsible for that plant; the spend, "
               "tied to that building rather than the whole contract"},
    {"question": "the building using the most energy per square metre - what reactive work has it "
                 "raised this quarter, and is any of it plant that would explain the consumption",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "the building; its reactive work this quarter; which of that work is plant, and "
               "whether it accounts for the consumption or does not"},
    {"question": "take the building with the most overdue work orders - what is open there, are its "
                 "vendors inside sla, and how does its energy use compare with the rest",
     "needs": ("wo_engine", "contract_performance", "energy_intelligence"),
     "covers": "which building; its open work; its vendors' SLA position; its energy use against "
               "the portfolio, as a comparison and not a bare figure"},
    {"question": "how many jobs did we close last month, and did the vendors who did them meet their sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the count closed; the vendors who did them; each one's SLA position, not a single "
               "portfolio average"},
    {"question": "which contracts come up for renewal, and do those vendors have any lapsed "
                 "certificates",
     "needs": ("compliance", "contract_performance"),
     "covers": "the contracts due for renewal; each of those vendors' lapsed certificates; the "
               "renewals that carry a compliance problem called out as such"},
    {"question": "who can replace a blocked vendor, and what is that replacement costing us under "
                 "contract",
     "needs": ("compliance", "contract_performance"),
     "covers": "which vendor is blocked and why; who can replace them; what the replacement costs "
               "against the blocked vendor's contract"},
    {"question": "for any certificate lapsing in the next 30 days, has remedial work been raised and "
                 "is the vendor responsible inside their sla",
     "needs": ("compliance", "wo_engine", "contract_performance"),
     "covers": "the certificates lapsing; whether remedial work exists for each; the responsible "
               "vendor's SLA position; the ones with no work raised called out"},
    # ── fifty more, ten per area (6 Nov 2026) ──────────────────────────────────────────────
    #
    # Asked for as "10 questions for ops, assets, compliance, energy and maintenance, interlinked
    # cross domain". Every one was put through resolve_phase2_engine BEFORE it was added and kept
    # only if it routes INTO an area it needs -- the router reads the opening, so a three-part
    # question that opens on the wrong area lands with an engine owning no part of it. 59 of 64
    # candidates survived that; the five that did not are recorded under the assets block below.
    #
    # `covers` is what an answer is judged against and never an expected answer: only a person
    # reading real rows can write one of those.

    # ── ops ────────────────────────────────────────────────────────────────────────────────
    {"question": "which building has the most open work orders right now, are its vendors inside "
                 "sla, and what is the oldest job still sitting there",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "which building, by count; that building's vendors and each one's SLA position; "
               "the oldest open job named with its age, not just a total"},
    {"question": "the oldest open work order on site - what is it, who is it with, and is that "
                 "contractor meeting their response sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the work order identified; the contractor holding it; that contractor's response "
               "SLA position, judged from the record rather than asserted"},
    {"question": "how many jobs were raised this month, how many did we close, and did the "
                 "contractors who did them hit their first fix rate",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "raised and closed as two separate counts; the contractors involved; first fix "
               "rate per contractor, not one portfolio average"},
    {"question": "which work orders have been reopened, and is the contractor who first attended "
                 "them still inside their sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the reopened work orders; who attended first; whether a reopen is counted against "
               "that contractor's SLA, stated either way"},
    {"question": "what work is open at my worst building, and does that building have any lapsed "
                 "certificates",
     "needs": ("wo_engine", "compliance"),
     "covers": "what makes it the worst, said out loud; the open work there; its lapsed "
               "certificates, with none reported as none rather than omitted"},
    {"question": "show me the work orders waiting on approval, and whether the vendors attached to "
                 "them are blocked",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the work orders awaiting approval; the vendor on each; which of those vendors are "
               "blocked, since approving work to a blocked vendor is the risk"},
    {"question": "which jobs breached their response time last month, and what did those "
                 "contractors invoice us for them",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the breaching jobs; the contractor on each; what was invoiced, tied to those jobs "
               "and not the contractor's whole month"},
    {"question": "what reactive work is open on plant, and is any of that plant in a building "
                 "where consumption is rising",
     "needs": ("wo_engine", "energy_intelligence"),
     "covers": "the open reactive work on plant; which buildings it sits in; which of those have "
               "rising consumption, with the overlap named rather than two lists"},
    {"question": "which work orders are overdue, and do any of them close out an inspection "
                 "recommendation",
     "needs": ("wo_engine", "compliance"),
     "covers": "the overdue work orders; which carry an inspection recommendation; the ones where "
               "an overdue job is holding a recommendation open called out as such"},
    {"question": "which jobs are unassigned, and which contractor would normally take them under "
                 "the contract",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the unassigned jobs; the contractor the contract puts them with; any job with no "
               "contractor under any contract said plainly"},

    # ── maintenance ────────────────────────────────────────────────────────────────────────
    {"question": "which planned maintenance visits were missed, and did a reactive job follow at "
                 "the same building",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the missed planned visits; reactive work at the same building afterwards; whether "
               "the two are connected, or that it cannot be told from the record"},
    {"question": "what planned work is due in the next 30 days, and are the contractors who do it "
                 "inside sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the planned work due, by date; the contractor for each; each contractor's current "
               "SLA position, so a due visit with a failing contractor stands out"},
    {"question": "what planned work is overdue, and does any of it carry a statutory inspection",
     "needs": ("wo_engine", "compliance"),
     "covers": "the overdue planned work; which items are statutory; statutory overdue separated "
               "from ordinary overdue, because the consequence differs"},
    {"question": "is our planned maintenance keeping up with the reactive load, and which "
                 "contractor is worst",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "planned against reactive as a ratio or comparison, not two counts; the worst "
               "contractor named with the figure that makes them worst"},
    {"question": "which planned visits were signed off without a certificate being filed",
     "needs": ("wo_engine", "compliance"),
     "covers": "the signed-off visits; which have no certificate on file; that a missing "
               "certificate is a gap in the record, not proof the work was not done"},
    {"question": "what maintenance has been raised on the plant driving our highest consumption",
     "needs": ("wo_engine", "energy_intelligence"),
     "covers": "which plant drives the consumption; the maintenance raised against it; whether "
               "that maintenance would explain the consumption or does not"},
    {"question": "ppm compliance by contractor this quarter, and which of them are also missing "
                 "their sla",
     "needs": ("contract_performance", "wo_engine"),
     "covers": "PPM compliance per contractor; their SLA position; the contractors failing both, "
               "which is the answer to the question as asked"},
    {"question": "which contractors are behind on their planned visits, and what are we paying "
                 "them",
     "needs": ("contract_performance", "wo_engine"),
     "covers": "the contractors behind, with how far behind; what each is paid; the two set "
               "against each other rather than listed one after the other"},
    {"question": "how many planned jobs did we close last quarter, and did the vendors who did "
                 "them meet their sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the count closed; the vendors who did them; each vendor's SLA position on that "
               "planned work specifically"},
    {"question": "which buildings have planned work slipping, and how does their energy use "
                 "compare",
     "needs": ("wo_engine", "energy_intelligence"),
     "covers": "the buildings where planned work is slipping; their energy use against the rest "
               "of the portfolio, as a comparison and not a bare figure"},

    # ── assets ─────────────────────────────────────────────────────────────────────────────
    #
    # Five asset questions had to be rewritten to be usable at all, because the router returns
    # None for them: "which plant has failed most this year", "which equipment failed more than
    # twice this year", "what work is open on the oldest plant", "which plant is out of service",
    # "which assets have had more reactive jobs than planned visits". Failure, age and in-service
    # state are ordinary things to ask an asset register, and resolve_phase2_engine places none
    # of them. Naming the asset type, or anchoring on "work order", routes; the gap is the
    # router's vocabulary, and it is recorded here rather than hidden by the rewrite.
    {"question": "what work orders have the chillers needed, and what are those buildings "
                 "consuming",
     "needs": ("wo_engine", "energy_intelligence"),
     "covers": "the work orders against chillers; the consumption of the buildings they sit in; "
               "whether the work and the consumption line up"},
    {"question": "which assets are costing the most in work orders, and how does that sit against "
                 "the contract we are on",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the assets by work order cost; the contract covering them; whether the spend is "
               "inside what the contract already pays for or on top of it"},
    {"question": "what equipment has an open work order right now, and is the contractor on it "
                 "inside sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the equipment with open work; the contractor on each; that contractor's SLA "
               "position on that job, not their portfolio average"},
    {"question": "where is consumption highest, and what plant maintenance has been raised there",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "the building with the highest consumption, with the measure named; the plant "
               "maintenance raised there; whether it accounts for the consumption"},
    {"question": "which assets have a lapsed statutory certificate, and is there work raised to "
                 "recertify them",
     "needs": ("compliance", "wo_engine"),
     "covers": "the assets with a lapsed statutory certificate; recertification work for each; "
               "the assets lapsed with nothing raised named explicitly"},
    {"question": "what work have the boilers needed this year, and is the contractor who services "
                 "them inside sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the boiler work this year; the servicing contractor; their SLA position, judged "
               "from the record rather than asserted"},
    {"question": "which equipment has both an open work order and a certificate expiring in the "
                 "next 90 days",
     "needs": ("wo_engine", "compliance"),
     "covers": "equipment meeting BOTH conditions, as an intersection and not two lists; the "
               "expiry date for each; an empty intersection said plainly"},
    {"question": "what jobs are open on lifts, and is the lift contractor inside its sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the open lift jobs; the lift contractor; their SLA position, with lift work "
               "treated as the statutory-adjacent work it is"},
    {"question": "what work orders are open on the air handling units, and is the contractor on "
                 "them inside sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the open AHU work orders; the contractor on them; their SLA position on that "
               "work, with none found reported as none"},
    {"question": "which assets have open work orders at the building with the highest consumption",
     "needs": ("wo_engine", "energy_intelligence"),
     "covers": "which building consumes most, named first; the assets there with open work; the "
               "two joined, rather than the open work for the whole portfolio"},

    # ── compliance ─────────────────────────────────────────────────────────────────────────
    {"question": "which certificates lapse in the next 60 days, and is there work raised to renew "
                 "them",
     "needs": ("compliance", "wo_engine"),
     "covers": "the certificates lapsing, by date; renewal work for each; the ones lapsing with "
               "nothing raised, which is the point of asking"},
    {"question": "which buildings are failing compliance, and are the contractors responsible "
                 "inside their sla",
     "needs": ("compliance", "contract_performance"),
     "covers": "the failing buildings and what fails; the contractor responsible for each; their "
               "SLA position, so a failing building with a failing contractor is visible"},
    {"question": "what did the last fire risk assessment recommend, and has any of it been raised "
                 "as a work order",
     "needs": ("compliance", "wo_engine"),
     "covers": "the recommendations from the assessment; work raised against each; the "
               "recommendations with nothing raised named one by one"},
    {"question": "which vendors are blocked on compliance, and what work is still assigned to them",
     "needs": ("compliance", "wo_engine"),
     "covers": "the blocked vendors and why each is blocked; work still assigned to them; that "
               "open work with a blocked vendor is the exposure, said as such"},
    {"question": "which statutory inspections are overdue, and what are we paying the contractor "
                 "who should do them",
     "needs": ("compliance", "contract_performance"),
     "covers": "the overdue statutory inspections; the contractor obliged to do each; what that "
               "contractor is paid, set against work not done"},
    {"question": "which certificates expired without being renewed, and what work followed at "
                 "those buildings",
     "needs": ("compliance", "wo_engine"),
     "covers": "the expired, unrenewed certificates; work at those buildings afterwards; whether "
               "any of that work addressed the expiry or none did"},
    {"question": "are any of my highest consumption buildings also failing compliance",
     "needs": ("compliance", "energy_intelligence"),
     "covers": "the highest consumption buildings, with the measure; their compliance position; "
               "the overlap answered as a yes or no before anything is listed"},
    {"question": "which contract terms come up for renewal with compliance obligations still open",
     "needs": ("compliance", "contract_performance"),
     "covers": "the contracts due for renewal; the open compliance obligations under each; the "
               "renewals that would carry an unmet obligation forward"},
    {"question": "what inspection recommendations are open with no work order against them",
     "needs": ("compliance", "wo_engine"),
     "covers": "the open recommendations; which have work raised; the ones with nothing raised, "
               "which is what was actually asked for"},
    {"question": "which vendor has the most lapsed certificates, and are they still being given "
                 "work",
     "needs": ("compliance", "wo_engine"),
     "covers": "the vendor, with the count; what has lapsed; whether work is still being assigned "
               "to them, which is the decision the question is really about"},

    # ── energy ─────────────────────────────────────────────────────────────────────────────
    {"question": "where is consumption highest per square metre, and what work has been raised on "
                 "the plant there",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "the building by intensity, not by total; the plant work raised there; whether "
               "that work explains the intensity or leaves it unexplained"},
    {"question": "which buildings have seen energy use rise most this quarter, and is the plant "
                 "there overdue maintenance",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "the buildings by rise, with the figures; the plant there; whether its maintenance "
               "is overdue, tying the rise to the maintenance or declining to"},
    {"question": "what are we spending on energy, and how does that compare with what we pay our "
                 "contractors",
     "needs": ("energy_intelligence", "contract_performance"),
     "covers": "energy spend; contractor spend; the two compared on the same basis and period, "
               "which is where this answer usually goes wrong"},
    {"question": "which buildings are our worst energy performers, and do they have open "
                 "compliance issues",
     "needs": ("energy_intelligence", "compliance"),
     "covers": "the worst performers, with the measure named; their open compliance issues; the "
               "buildings carrying both, rather than two separate lists"},
    {"question": "where has consumption spiked, and was there a work order at that building around "
                 "the same time",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "where and when consumption spiked; work orders at that building near that date; "
               "whether the timing connects them, stated as uncertain if it is"},
    {"question": "which sites have the worst energy intensity, and what have they cost us in work "
                 "orders",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "the sites by intensity; their work order cost; the two set against each other, "
               "since a cheap site to maintain may be an expensive one to run"},
    {"question": "has energy use fallen where we have done plant replacement work",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "where plant replacement happened; consumption before and after at those "
               "buildings; an honest answer where the record cannot show it"},
    {"question": "which building costs us most on energy and on maintenance together",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "energy cost and maintenance cost per building, then combined; the total that "
               "decides the answer, not whichever figure is larger"},
    {"question": "where is consumption rising while planned maintenance is behind",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "buildings meeting both conditions, as an intersection; how far behind the planned "
               "maintenance is; an empty intersection said plainly"},
    {"question": "which buildings have an energy certificate on record and rising consumption",
     "needs": ("energy_intelligence", "compliance"),
     "covers": "the buildings with a certificate on record, with its rating; their consumption "
               "trend; where a good rating sits against a rising trend"},
)

#: How the question is typed. The point of these is that a real question is rarely tidy.
STYLES = ("neutral", "short", "typos", "rambling", "frustrated")

#: How hard the question is to answer, from the skill's Difficulty dimension.
DIFFICULTIES = ("clear", "vague", "missing information", "on a limit")


@dataclass(frozen=True)
class Scenario:
    """One generated question, and everything needed to judge the answer afterwards."""
    scenario_id: str
    space: str
    engine: str
    question: str
    role: str
    style: str
    difficulty: str
    #: False for a challenge scenario: asked in a space that must REFUSE it. Kept deliberately,
    #: because a gate that stops working is a silent failure otherwise.
    in_module: bool = True
    #: The areas a cross-domain question spans, empty for a single-area one. A reviewer needs this
    #: to judge the answer: one that covers the first part well and drops the rest reads as a good
    #: answer and is a failure.
    needs: tuple[str, ...] = ()
    #: What a good answer has to address. NOT an expected answer -- only a person reading real
    #: rows can write one of those -- but what to check it against.
    covers: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Question stems a person in a hurry drops. Longest first, so "what is the" wins over "what is".
_SHORT_STEMS: tuple[str, ...] = (
    "which of the", "what is the", "how many of the", "how much of the", "tell me which",
    "show me the", "show me", "what are the", "what is", "what are", "how many", "how much",
    "how long are", "how long", "which", "what", "who can", "who", "where is", "where", "when is",
    "when", "is each", "are any", "is any", "has any", "have any", "list", "do we", "did we",
)

#: Words that carry nothing once the stem is gone.
_SHORT_FILLER = frozenset({"is", "are", "was", "were", "do", "does", "did", "the", "a", "an",
                           "right", "now", "us", "we", "our", "my", "me"})


def _rough(question: str, style: str, rng: random.Random) -> str:
    """The same question as somebody would really type it."""
    if style == "short":
        # Clipped, not truncated. Taking the first four words turned "which contractor overran on
        # cost last quarter" into "which contractor overran on" and "how many work orders are open
        # at each building" into "how many work orders" -- questions that mean nothing, which
        # measure the engine's handling of nonsense rather than of terseness. Somebody in a hurry
        # drops the question stem and keeps the subject, so that is what this does.
        words = question.split()
        for stem in _SHORT_STEMS:
            parts = stem.split()
            if [w.lower() for w in words[:len(parts)]] == parts:
                words = words[len(parts):]
                break
        words = [w for w in words if w.lower() not in _SHORT_FILLER]
        return " ".join(words) if len(words) >= 2 else question
    if style == "typos":
        out = list(question)
        for _ in range(max(1, len(out) // 25)):
            i = rng.randrange(len(out))
            if out[i].isalpha():
                out[i] = ""
        return "".join(out)
    if style == "rambling":
        return ("sorry quick one, I know you get asked this a lot but " + question
                + " - just need it for the morning meeting")
    if style == "frustrated":
        return question + "? this is the third time I am asking"
    return question


def engines_of(space: str) -> tuple[str, ...]:
    """The engines a space may use, in a fixed order so a seed reproduces a batch."""
    from src.agents.spaces import engines_for

    allowed = engines_for(space)
    names = sorted(BY_ENGINE) if allowed is None else sorted(e for e in BY_ENGINE if e in allowed)
    return tuple(names)


def routes_to(question: str) -> str:
    """Where the real router sends this question. The generator never guesses it."""
    from src.agents.phase2_intents import resolve_phase2_engine

    return resolve_phase2_engine(user_message=question, context_note=None)


def generate(space: str, *, count: int = 50, seed: int = 0,
             challenge_ratio: float = 0.0) -> list[Scenario]:
    """A batch for one space: `count` questions it should answer, verified against the router.

    A question whose ROUTED engine is not one this space has is dropped, not shipped -- it would
    be refused, and a refusal scored as an answer measures the gate. challenge_ratio adds
    deliberate out-of-module questions, marked in_module=False, for checking the gate itself.
    """
    from src.agents.spaces import allows

    if space not in SPACES:
        raise ValueError(f"unknown space {space!r}; one of {SPACES}")
    rng = random.Random(f"{space}:{seed}")
    mine = engines_of(space)
    if not mine:
        return []

    out: list[Scenario] = []
    # The BASE question, not the typed-out one. Dedup on the surface form let the same question
    # through three times in one batch of ten -- once plainly, once with a typo, once rambling --
    # because "is each vendor inside its sla", "is each endor inside its sla" and "sorry quick
    # one, ... is each vendor inside its sla" are three different strings and one question. A
    # reviewer reading the same question three times is being asked to grade one answer three
    # times, which is worse than a short batch.
    used: set[str] = set()
    seen: set[str] = set()
    n_challenge = int(count * challenge_ratio)
    n_main = count - n_challenge

    # Styles and roles are DEALT from a shuffled deck, not drawn independently. Independent draws
    # gave a batch of ten with three questions ending "this is the third time I am asking" and two
    # opening "sorry quick one, I know you get asked this a lot but" -- which reads as padding and
    # measures one writing style three times instead of five styles twice.
    styles = _deck(STYLES, rng)
    roles = _deck(ROLES, rng)
    hardness = _deck(DIFFICULTIES, rng)

    # -- the questions this space is for -------------------------------------------------
    #
    # One pass over the pool, dealt round-robin across the engines, rather than random draws with
    # an attempt budget. Random draws plus "skip a base already used" meant that once one engine's
    # questions were spent, the position that engine's turn fell on could never be filled, and the
    # batch came back short while unused questions sat in the other engines.
    pool: dict[str, list[str]] = {}
    for e in mine:
        deck = list(BY_ENGINE[e])
        rng.shuffle(deck)
        pool[e] = deck
    candidates: list[tuple[str, str]] = []
    while any(pool.values()):
        for e in mine:
            if pool[e]:
                candidates.append((e, pool[e].pop()))

    # Cross-domain questions FIRST, and only those whose every area this instance has. They are
    # the ones worth asking -- a single lookup is easy and a question with three parts is where an
    # answer quietly drops two of them -- so a batch too small to hold everything holds these.
    cross = [c for c in CROSS_DOMAIN if all(e in mine and allows(space, e) for e in c["needs"])]
    rng.shuffle(cross)
    # Not the whole batch: a batch of only multi-part questions stops measuring the plain ones
    # that make up most of what people ask. Roughly a third, and at least one where any fit.
    room = max(1, round(n_main / 3)) if cross else 0
    for c in cross[:room]:
        if len(out) >= n_main:
            break
        question = c["question"]
        if question in seen:
            continue
        routed = routes_to(question)
        # Stricter than for a single question: it must land in an area the question NEEDS. Routed
        # anywhere else, the engine that picks it up owns no part of the question, and the answer
        # measures the router rather than the engines.
        if routed not in c["needs"] or not allows(space, routed):
            continue
        seen.add(question)
        used.add(question)
        out.append(Scenario(
            scenario_id=_sid(space, seed, len(out)), space=space, engine=routed, question=question,
            role=roles(len(out)), style="neutral", difficulty="on a limit",
            needs=tuple(c["needs"]), covers=c["covers"]))

    for engine, base in candidates:
        if len(out) >= n_main:
            break
        if base in used:
            continue
        # The style for this position, with the plain question as a fallback. Roughening can break
        # routing -- dropped letters especially -- and the question matters more than the typing,
        # so a style that will not route gives way to the plain one rather than losing the
        # question.
        routed = None
        chosen = "neutral"
        for style in (styles(len(out)), "neutral"):
            question = _rough(base, style, rng)
            if question in seen:
                continue
            # The check that makes this a generator rather than a list. Both halves matter: the
            # router has to send it to a domain engine this space HAS, and the space has to allow
            # that engine.
            cand = routes_to(question)
            if cand in mine and allows(space, cand):
                routed, chosen = cand, style
                break
        if routed is None:
            continue
        seen.add(question)
        used.add(base)
        out.append(Scenario(
            scenario_id=_sid(space, seed, len(out)), space=space, engine=routed, question=question,
            role=roles(len(out)), style=chosen, difficulty=hardness(len(out))))

    # -- questions the space must refuse ---------------------------------------------------
    # Only as many as were ASKED for. This used to run until the batch reached `count`, so a main
    # loop that came up short was padded with questions the instance must REFUSE -- an Ops batch
    # requested with no refusals came back as seven refusals, every answer a refusal, which reads
    # as the engine failing rather than the generator running out.
    outside = sorted(e for e in BY_ENGINE if e not in mine)
    want = len(out) + n_challenge
    attempts = 0
    while len(out) < want and outside and attempts < n_challenge * 60 + 60:
        attempts += 1
        engine = outside[len(out) % len(outside)]
        question = rng.choice(BY_ENGINE[engine])
        if question in seen or question in used:
            continue
        if routes_to(question) != engine or allows(space, engine):
            continue
        seen.add(question)
        used.add(question)
        out.append(Scenario(
            scenario_id=_sid(space, seed, len(out)), space=space, engine=engine, question=question,
            role=roles(len(out)), style="neutral", difficulty="clear", in_module=False))
    return out


def _deck(values: tuple[str, ...], rng: random.Random):
    """Deal `values` evenly, shuffling again each time the deck runs out.

    Every value appears once before any appears twice, so a batch of ten over five styles is two
    of each rather than whatever five independent draws happened to give. The shuffle keeps the
    order unpredictable and keeps it a function of the seed, so a batch is still reproducible.
    """
    order: list[str] = []

    def take(i: int) -> str:
        while len(order) <= i:
            nxt = list(values)
            rng.shuffle(nxt)
            order.extend(nxt)
        return order[i]

    return take


def _sid(space: str, seed: int, i: int) -> str:
    return f"{space.upper()}-{seed}-{i + 1:03d}"


def pool_of(space: str) -> int:
    """How many questions are written for this space: its engines' own, and the cross-domain ones
    whose every area it has. 10 Nov 2026: the cross-domain questions (6 Nov) were left out, so an
    Ops batch of 62 said it came from a pool of 53."""
    from src.agents.spaces import allows

    mine = engines_of(space)
    cross = sum(1 for c in CROSS_DOMAIN if all(e in mine and allows(space, e) for e in c["needs"]))
    return sum(len(BY_ENGINE[e]) for e in mine) + cross


def batch(space: str, *, count: int = 50, seed: int = 0,
          challenge_ratio: float = 0.0) -> dict[str, Any]:
    """A generated batch as the golden-set upload takes it: one row per question."""
    rows = generate(space, count=count, seed=seed, challenge_ratio=challenge_ratio)
    return {
        "space": space, "seed": seed, "requested": count, "generated": len(rows),
        "engines": list(engines_of(space)),
        "by_engine": {e: sum(1 for r in rows if r.engine == e) for e in engines_of(space)},
        "out_of_module": sum(1 for r in rows if not r.in_module),
        #: How many need more than one area. A batch with none of these measures only the easy
        #: half of what people ask.
        "cross_domain": sum(1 for r in rows if r.needs),
        # The ceiling, so "7 of 25" reads as "this instance has seven" rather than as a failure.
        # Every question is used at most once in a batch, so the pool IS the largest batch.
        "pool": pool_of(space),
        "scenarios": [r.as_dict() for r in rows],
        "fingerprint": fingerprint(rows),
    }


def fingerprint(rows: Iterable[Scenario]) -> str:
    """Twelve hex of the batch, so a run can say which batch it measured."""
    h = hashlib.sha256()
    for r in rows:
        h.update((r.scenario_id + "|" + r.question + "|" + r.engine).encode("utf-8"))
    return h.hexdigest()[:12]

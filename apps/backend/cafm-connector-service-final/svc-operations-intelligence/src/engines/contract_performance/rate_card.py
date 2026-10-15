"""The rate card a contract actually states — per trade, per hour, in its own currency.

`contract_sla_parameters` has one scalar for labour: `labour_day_rate`. Real FM contracts
are frequently not priced that way. `04_WKU_Facilities-Management-SLA-2022.pdf`, read
against its source on 17 Sep 2026, carries twelve trades in Appendix B, each with a
straight and an overtime hourly figure:

    Area Technicians  44.82 / 67.23      Plumbers    51.71 / 77.56
    Electricians      48.20 / 73.29      HVAC        51.40 / 77.10
    Painters          34.12 / 51.17      Custodial   18.18 / 27.27   (+6 more)

None of it was stored, because CONTRACT_FIELDS — joined straight into the extraction prompt
— asked only for a day rate. The panel then showed £350/day marked "default" and told the
reader the contract "did not state" a rate. Every WKU invoice line would have been checked
against a number nobody wrote down, in the wrong currency.

STORAGE IS A JSONB COLUMN THAT DOES NOT EXIST YET.

`rate_card_json` needs one ALTER TABLE, and this service does not write DDL to production.
Naming a column that is not there fails the whole statement at parse time, so every access
probes the catalogue once per process — the pattern `vendor_contacts.is_primary` already
uses in engines/compliance/contractors.py — and falls back to today's behaviour when the
column is absent. The code therefore ships dark and turns on the moment the migration runs,
with no second deployment.

Shape:

    {"currency": "USD", "basis": "hour", "source": "Appendix B",
     "lines": [{"trade": "HVAC", "straight": 51.40, "overtime": 77.10}, ...]}

JSONB rather than a `contract_rate_lines` table because a card belongs to exactly one
contract and is always read whole — nothing queries across cards, so per-line indexing
would buy nothing — and because every other structured term on this row is already JSONB.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

#: None until probed. See the module docstring: the column is not in production yet.
_HAS_RATE_CARD: bool | None = None


async def column_present(session: AsyncSession) -> bool:
    """Whether contract_sla_parameters.rate_card_json exists here. Read once per process."""
    global _HAS_RATE_CARD
    if _HAS_RATE_CARD is None:
        try:
            async with session.begin_nested():
                found = (
                    await session.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_schema = 'plenum_cafm' "
                            "AND table_name = 'contract_sla_parameters' "
                            "AND column_name = 'rate_card_json' LIMIT 1"
                        )
                    )
                ).scalar()
            _HAS_RATE_CARD = bool(found)
            if not _HAS_RATE_CARD:
                log.info(
                    "contract.rate_card_column_absent",
                    detail=(
                        "rate_card_json is not on this database; per-trade rates read from a "
                        "contract cannot be stored until the migration runs"
                    ),
                )
        except Exception as exc:  # noqa: BLE001 — an unreadable catalogue costs the card,
            # never the ingest. A contract without its rate card is still worth having.
            log.warning("contract.rate_card_probe_failed", error=str(exc)[:160])
            _HAS_RATE_CARD = False
    return _HAS_RATE_CARD


def _num(v: Any) -> float | None:
    """A rate as a number, or None. Models return "51.71" as often as 51.71."""
    if v is None or isinstance(v, bool):
        return None
    try:
        out = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    # A negative or absurd rate is a misread, not a price.
    return out if 0 < out < 100_000 else None


def normalise(raw: Any) -> dict[str, Any] | None:
    """The card as it should be stored, or None when there is nothing worth storing.

    An empty shell is worse than nothing: it renders as "a rate card is on file" while
    holding no rate, which is the same misdirection `labour_day_rate: 350` produced.
    """
    if not isinstance(raw, dict):
        return None
    lines: list[dict[str, Any]] = []
    for ln in raw.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        trade = str(ln.get("trade") or ln.get("name") or "").strip()
        straight = _num(ln.get("straight") if "straight" in ln else ln.get("rate"))
        overtime = _num(ln.get("overtime"))
        # A trade with no rate at all is a row of the document, not a price.
        if not trade or (straight is None and overtime is None):
            continue
        lines.append({"trade": trade, "straight": straight, "overtime": overtime})
    if not lines:
        return None
    basis = str(raw.get("basis") or "").strip().lower() or None
    if basis not in (None, "hour", "day", "visit"):
        basis = None
    cur = str(raw.get("currency") or "").strip().upper() or None
    return {
        # Left as None when the document does not say. The panel renders the platform's
        # currency symbol, and these rates are dollars — inheriting £ would be a new lie in
        # place of the one this replaces.
        "currency": cur if cur and len(cur) <= 8 else None,
        "basis": basis,
        "source": (str(raw.get("source") or "").strip() or None),
        "lines": lines,
    }


def rate_for(card: dict[str, Any] | None, trade: str | None, *, overtime: bool = False) -> float | None:
    """The stated rate for one trade, or None.

    None means "this contract does not price that trade" and an invoice check must say so
    rather than reach for a neighbouring number. Comparing a roofer's line against the HVAC
    rate is the same class of error as comparing it against a £350/day default.
    """
    if not card or not trade:
        return None
    want = str(trade).strip().lower()
    for ln in card.get("lines") or []:
        if str(ln.get("trade") or "").strip().lower() == want:
            return ln.get("overtime") if overtime else ln.get("straight")
    return None


async def read_for(session: AsyncSession, parameters_id: Any) -> dict[str, Any] | None:
    """The stored card for a contract, or None when the column or the row has none."""
    if not await column_present(session):
        return None
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        "SELECT rate_card_json FROM plenum_cafm.contract_sla_parameters "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": str(parameters_id)},
                )
            ).mappings().first()
        return normalise(row["rate_card_json"]) if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("contract.rate_card_read_failed", error=str(exc)[:160])
        return None


async def write_for(session: AsyncSession, parameters_id: Any, raw: Any) -> bool:
    """Store the card. Returns whether it was written.

    False is a normal outcome, not an error: it means the column is not on this database
    yet, and the contract is ingested without its rate card exactly as it is today.
    """
    card = normalise(raw)
    if card is None or not await column_present(session):
        return False
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "UPDATE plenum_cafm.contract_sla_parameters "
                    "SET rate_card_json = CAST(:card AS jsonb) WHERE id = CAST(:id AS uuid)"
                ),
                {"card": __import__("json").dumps(card), "id": str(parameters_id)},
            )
        log.info(
            "contract.rate_card_stored",
            parameters_id=str(parameters_id),
            lines=len(card["lines"]),
            currency=card["currency"],
            basis=card["basis"],
        )
        return True
    except Exception as exc:  # noqa: BLE001 — the parameters are the point; the card is
        # an enrichment, and losing it must not lose the ingest.
        log.warning("contract.rate_card_write_failed", error=str(exc)[:200])
        return False

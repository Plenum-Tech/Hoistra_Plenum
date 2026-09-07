"""Live register bot search (UKAS / BPCA / BASIS) + auto-verify scoring.

Uses Playwright when installed; otherwise dump/file hits only.
On a confident company-name match, CCC marks the certificate verified.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

from ....config import settings
from ....core.logging import get_logger

log = get_logger(__name__)

_BOT_REGISTERS = frozenset({"ukas", "bpca", "basis", "hse", "sia"})


def normalize_name(value: str | None) -> str:
    s = (value or "").upper().strip()
    s = re.sub(r"[^\w\s&]", " ", s)
    s = re.sub(r"\b(LIMITED|LTD|PLC|LLP|INC|LLC|THE)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def name_match_confidence(query: str, candidate: str | None) -> float:
    """0–1 score for company-name match (exact / contains / token overlap)."""
    q = normalize_name(query)
    c = normalize_name(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.92
    q_tokens = set(q.split())
    c_tokens = set(c.split())
    if not q_tokens or not c_tokens:
        return 0.0
    overlap = len(q_tokens & c_tokens) / len(q_tokens)
    if overlap >= 0.8:
        return 0.88
    if overlap >= 0.6:
        return 0.75
    return 0.0


def score_hits(name: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for h in hits:
        conf = max(
            name_match_confidence(name, h.get("company_name")),
            name_match_confidence(name, h.get("lookup_key")),
        )
        row = dict(h)
        row["match_confidence"] = round(conf, 3)
        scored.append(row)
    scored.sort(key=lambda r: float(r.get("match_confidence") or 0), reverse=True)
    return scored


def select_auto_verify_hit(
    name: str,
    hits: list[dict[str, Any]],
    *,
    min_confidence: float | None = None,
) -> dict[str, Any] | None:
    """Pick a hit strong enough to auto-mark verified."""
    threshold = (
        min_confidence
        if min_confidence is not None
        else float(getattr(settings, "register_bot_min_confidence", 0.85) or 0.85)
    )
    if not getattr(settings, "register_bot_auto_verify", True):
        return None
    scored = score_hits(name, hits)
    if not scored:
        return None
    top = scored[0]
    conf = float(top.get("match_confidence") or 0)
    if conf < threshold:
        return None
    # Ambiguous: two high-confidence different companies → human
    if len(scored) > 1:
        second = float(scored[1].get("match_confidence") or 0)
        top_name = normalize_name(top.get("company_name"))
        second_name = normalize_name(scored[1].get("company_name"))
        if second >= threshold and top_name and second_name and top_name != second_name:
            return None
    return top


async def run_live_bot_search(
    *,
    register: str,
    name: str,
    limit: int = 10,
) -> dict[str, Any]:
    """
    Browser-bot search against official portals.
    Returns {ok, results, engine, notes, deep_link_used}.
    """
    reg = (register or "").lower().strip()
    q = (name or "").strip()
    if reg not in _BOT_REGISTERS:
        return {"ok": False, "error": f"unsupported_register:{register}", "results": []}
    if not q:
        return {"ok": False, "error": "name_required", "results": []}
    if not getattr(settings, "register_bot_enabled", True):
        return {
            "ok": False,
            "error": "register_bot_disabled",
            "results": [],
            "notes": ["Set REGISTER_BOT_ENABLED=true to enable live bot search."],
        }

    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        return {
            "ok": False,
            "error": "playwright_not_installed",
            "results": [],
            "engine": None,
            "notes": [
                "Install playwright (`pip install playwright && playwright install chromium`) "
                "for live portal bot search. Dump name matches still auto-verify."
            ],
        }

    try:
        if reg == "ukas":
            results = await _bot_ukas(q, limit=limit)
        elif reg == "bpca":
            results = await _bot_bpca(q, limit=limit)
        elif reg == "basis":
            results = await _bot_basis(q, limit=limit)
        elif reg == "hse":
            # HSE list is dump-backed; live page is a PDF/HTML library — skip browser
            results = []
        elif reg == "sia":
            results = await _bot_sia(q, limit=limit)
        else:
            results = []
        results = score_hits(q, results)[:limit]
        return {
            "ok": True,
            "register": reg,
            "name": q,
            "results": results,
            "result_count": len(results),
            "engine": "playwright",
            "notes": [],
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("register_bot.live_failed", register=reg, error=str(exc)[:240])
        return {
            "ok": False,
            "error": f"bot_failed:{type(exc).__name__}",
            "results": [],
            "engine": "playwright",
            "notes": [str(exc)[:200]],
        }


async def _with_page(coro_factory):
    from playwright.async_api import async_playwright  # type: ignore

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                locale="en-GB",
            )
            page = await context.new_page()
            return await coro_factory(page)
        finally:
            await browser.close()


async def _bot_ukas(name: str, *, limit: int) -> list[dict[str, Any]]:
    url = f"https://www.ukas.com/find-an-organisation/?organisation_name={quote_plus(name)}"

    async def _run(page):
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(2500)
        # Try common result containers; fall back to link text containing the name
        texts: list[str] = await page.eval_on_selector_all(
            "a, h2, h3, .card-title, .organisation-name, .search-result",
            "els => els.map(e => (e.innerText || '').trim()).filter(Boolean)",
        )
        return _extract_named_hits(texts, name=name, register="ukas", limit=limit, source_url=url)

    return await _with_page(_run)


async def _bot_bpca(name: str, *, limit: int) -> list[dict[str, Any]]:
    url = "https://bpca.org.uk/find-a-pest-controller/"

    async def _run(page):
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(1500)
        # Fill any visible search/keyword box
        for sel in (
            "input[name='keyword']",
            "input[name='s']",
            "input[type='search']",
            "input[placeholder*='Search' i]",
            "input[placeholder*='company' i]",
            "input[placeholder*='name' i]",
        ):
            box = await page.query_selector(sel)
            if box and await box.is_visible():
                await box.fill(name)
                break
        for sel in ("button[type='submit']", "input[type='submit']", "button:has-text('Search')"):
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                break
        else:
            await page.goto(
                f"https://bpca.org.uk/find-a-pest-controller/?s={quote_plus(name)}",
                wait_until="domcontentloaded",
                timeout=45_000,
            )
        await page.wait_for_timeout(2500)
        texts: list[str] = await page.eval_on_selector_all(
            "a, h2, h3, .member-name, .result-title, .card-title",
            "els => els.map(e => (e.innerText || '').trim()).filter(Boolean)",
        )
        return _extract_named_hits(
            texts, name=name, register="bpca", limit=limit, source_url=page.url
        )

    return await _with_page(_run)


async def _bot_basis(name: str, *, limit: int) -> list[dict[str, Any]]:
    url = f"https://basis-reg.co.uk/search-results?q={quote_plus(name)}"

    async def _run(page):
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        # Google CSE injects results async
        try:
            await page.wait_for_selector(".gsc-webResult, .gsc-result, .gs-title", timeout=20_000)
        except Exception:  # noqa: BLE001
            await page.wait_for_timeout(3000)
        texts: list[str] = await page.eval_on_selector_all(
            ".gs-title, .gsc-thumbnail-inside a, a.gs-title, h3, a",
            "els => els.map(e => (e.innerText || '').trim()).filter(Boolean)",
        )
        return _extract_named_hits(
            texts, name=name, register="basis", limit=limit, source_url=url
        )

    return await _with_page(_run)


async def _bot_sia(name: str, *, limit: int) -> list[dict[str, Any]]:
    url = "https://www.services.sia.homeoffice.gov.uk/Pages/acs-roac.aspx?all="

    async def _run(page):
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(2000)
        texts: list[str] = await page.eval_on_selector_all(
            "table tr td, a, li",
            "els => els.map(e => (e.innerText || '').trim()).filter(t => t && t.length < 200)",
        )
        return _extract_named_hits(texts, name=name, register="sia", limit=limit, source_url=url)

    return await _with_page(_run)


def _extract_named_hits(
    texts: list[str],
    *,
    name: str,
    register: str,
    limit: int,
    source_url: str,
) -> list[dict[str, Any]]:
    code_map = {
        "ukas": "UKAS_ASBESTOS",
        "bpca": "BPCA_MEMBER",
        "basis": "PESTICIDE_PAx",
        "sia": "SIA_ACS",
        "hse": "ASBESTOS_LICENCE",
    }
    code = code_map.get(register, register.upper())
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in texts:
        line = re.sub(r"\s+", " ", (raw or "").strip())
        if len(line) < 3 or len(line) > 160:
            continue
        # Skip nav chrome
        low = line.lower()
        if low in {"home", "search", "contact", "login", "find an organisation", "menu"}:
            continue
        conf = name_match_confidence(name, line)
        if conf < 0.6:
            continue
        key = normalize_name(line)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "certificate_type_code": code,
                "lookup_key": line[:120],
                "company_name": line[:160],
                "match_source": "live_bot",
                "source_file": None,
                "source_url": source_url,
                "match_confidence": round(conf, 3),
                "payload": {"company_name": line, "register": register},
            }
        )
        if len(out) >= limit:
            break
    return out



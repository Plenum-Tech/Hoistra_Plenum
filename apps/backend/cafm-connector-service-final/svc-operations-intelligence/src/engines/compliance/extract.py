"""Certificate field extraction against CountryPack key_fields_schema (A2/A3 ingest)."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from .country_pack import get_pack_type

log = get_logger(__name__)


async def extract_certificate_fields(
    session: AsyncSession,
    *,
    certificate_type_code: str,
    source_text: str | None = None,
    extracted_fields: dict[str, Any] | None = None,
    country_code: str = "UK",
    cert_scope: str | None = None,
    pdf_base64: str | None = None,
    extract_method: str | None = None,
) -> dict[str, Any]:
    """
    Apply pack key_fields_schema. Prefer caller-supplied extracted_fields
    (from Orchestrator/Claude). Uses Claude on source_text and/or full PDF
    (native document) when ANTHROPIC_API_KEY is set — required for scanned certs.
    """
    pack = await get_pack_type(session, certificate_type_code, country_code=country_code)
    if not pack:
        return {"ok": False, "error": f"Unknown certificate type {certificate_type_code}"}

    schema = pack.key_fields_schema or {}
    required = list(schema.get("required") or [])
    optional = list(schema.get("optional") or [])
    all_fields = required + optional

    fields = dict(extracted_fields or {})
    field_confidence: dict[str, str] = {}
    used_method = extract_method or "unknown"

    if not fields and settings.anthropic_api_key and (source_text or pdf_base64):
        fields, field_confidence = await _claude_extract(
            source_text=source_text or "",
            certificate_type_code=certificate_type_code,
            type_name=pack.certificate_type_name,
            fields=all_fields,
            pdf_base64=pdf_base64,
        )
        used_method = (
            "claude_pdf_fields"
            if pdf_base64
            else ("claude_text_fields" if source_text else used_method)
        )
    elif not fields and source_text:
        fields, field_confidence = _heuristic_extract(source_text, all_fields)
        used_method = "heuristic"
    else:
        for k in fields:
            field_confidence[k] = field_confidence.get(k) or "medium"

    # Heuristic pass to fill BAFE/accreditation gaps Claude may omit keys for
    if source_text and fields is not None:
        heur, hconf = _heuristic_extract(source_text, all_fields + [
            "inspector_accreditation_number",
            "BAFE registration no.",
            "BAFE Registered Organisation ID",
            # Sub-national grouping for reporting. A certificate almost always carries the
            # site address; without capturing it the register can only ever be grouped by
            # country, and "how does compliance look in Scotland" has no answer.
            "state",
            "region",
        ])
        for k, v in heur.items():
            if v and not fields.get(k):
                fields[k] = v
                field_confidence[k] = hconf.get(k) or "medium"

    missing_required = [f for f in required if not fields.get(f)]
    return {
        "ok": True,
        "requires_pm_confirmation": True,
        "certificate_type_code": certificate_type_code,
        "certificate_type_name": pack.certificate_type_name,
        "cert_scope": cert_scope or pack.certificate_scope,
        "key_fields_schema": schema,
        "extracted": fields,
        "field_confidence": field_confidence,
        "missing_required": missing_required,
        "extract_method": used_method,
        "message": (
            "PM must confirm or correct extracted fields before finalising "
            "(POST /certificates with confirmed_by_pm=true)."
        ),
        "verification_url": pack.verification_url,
        "required_contractor_accreditation": pack.required_contractor_accreditation,
    }


def _heuristic_extract(text: str, fields: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    out: dict[str, Any] = {}
    conf: dict[str, str] = {}

    _MONTHS = {
        "january": 1,
        "jan": 1,
        "february": 2,
        "feb": 2,
        "march": 3,
        "mar": 3,
        "april": 4,
        "apr": 4,
        "may": 5,
        "june": 6,
        "jun": 6,
        "july": 7,
        "jul": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sept": 9,
        "sep": 9,
        "october": 10,
        "oct": 10,
        "november": 11,
        "nov": 11,
        "december": 12,
        "dec": 12,
    }
    _month_alt = "|".join(_MONTHS.keys())
    # "28 February 2027" / "1 Jan 2026"
    _named_date = rf"(\d{{1,2}})\s+({_month_alt})\s+(20\d{{2}})"
    # Numeric UK / ISO
    _num_date = r"(\d{1,2}[/-]\d{1,2}[/-]20\d{2}|20\d{2}[/-]\d{1,2}[/-]\d{1,2})"

    def _norm_date(raw: str) -> str:
        raw = raw.strip().replace(".", "/")
        m_named = re.match(rf"^{_named_date}$", raw, re.I)
        if m_named:
            d, mon, y = m_named.groups()
            mo = _MONTHS[mon.lower()]
            return f"{y}-{mo:02d}-{int(d):02d}"
        # DD/MM/YYYY → YYYY-MM-DD (UK certificates)
        m_uk = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](20\d{2})$", raw)
        if m_uk:
            d, mo, y = m_uk.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        # YYYY-MM-DD or YYYY/MM/DD
        m_iso = re.match(r"^(20\d{2})[/-](\d{1,2})[/-](\d{1,2})$", raw)
        if m_iso:
            y, mo, d = m_iso.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        return raw.replace("/", "-")

    def _find_named_dates() -> list[str]:
        return [
            _norm_date(f"{d} {m} {y}")
            for d, m, y in re.findall(_named_date, text, re.I)
        ]

    # UK day-first + ISO year-first + month-name
    date_re = re.compile(rf"\b{_num_date}\b")
    # Compliance Engine / tabular SSOT rows win over narrative "Date" / BAFE reg lines
    engine_issue = re.search(
        rf"(?:^|\n)\s*issue_date\s*[|\t:]\s*(?:{_num_date}|{_named_date})",
        text,
        re.I,
    )
    engine_expiry = re.search(
        rf"(?:^|\n)\s*expiry_date\s*[|\t:]\s*(?:{_num_date}|{_named_date})",
        text,
        re.I,
    )
    engine_cert = re.search(
        r"(?:^|\n)\s*certificate_number\s*[|\t:]\s*([A-Z0-9][A-Z0-9\-/]{2,})",
        text,
        re.I,
    )
    labeled = {
        "issue": re.search(
            rf"(?:issue\s*date|date\s*of\s*(?:issue|inspection)|assessment\s*date|"
            rf"membership\s*year|valid\s*from|^date\s*[:|]|\bdate\t)\s*[:|]?\s*"
            rf"(?:{_num_date}|{_named_date})",
            text,
            re.I | re.M,
        ),
        "review": re.search(
            rf"(?:review\s*date|next\s*(?:review|inspection|exam|service)\s*due|"
            rf"next\s*due)\s*[:|]?\s*(?:{_num_date}|{_named_date})",
            text,
            re.I,
        ),
        "expiry": re.search(
            rf"(?:expiry(?:_date)?|expires|expiration|"
            rf"(?:certificate\s+)?valid\s+until|(?:certificate\s+)?valid\s+to)\s*[:|]?\s*"
            rf"(?:{_num_date}|{_named_date})",
            text,
            re.I,
        ),
    }
    dates = [_norm_date(d) for d in date_re.findall(text)]
    named_dates = _find_named_dates()
    all_dates = dates + [d for d in named_dates if d not in dates]

    def _group_date(m: re.Match[str] | None) -> str | None:
        if not m:
            return None
        # Prefer first capturing group that looks like a date fragment
        for g in m.groups():
            if not g:
                continue
            # Named date may arrive as separate groups — reconstruct from full match slice
            break
        raw = m.group(0)
        # Strip the label prefix; keep the trailing date token(s)
        dm = re.search(rf"(?:{_num_date}|{_named_date})\s*$", raw, re.I)
        if dm:
            return _norm_date(dm.group(0).strip())
        return None

    if "issue_date" in fields:
        if engine_issue:
            out["issue_date"] = _group_date(engine_issue) or _norm_date(engine_issue.group(1))
            conf["issue_date"] = "high"
        elif labeled["issue"]:
            got = _group_date(labeled["issue"])
            if got:
                out["issue_date"] = got
                conf["issue_date"] = "medium"
        elif all_dates:
            out["issue_date"] = all_dates[0]
            conf["issue_date"] = "low"

    if "expiry_date" in fields:
        # Prefer explicit expiry_date row; Review date is fallback for FRA/L8
        if engine_expiry:
            out["expiry_date"] = _group_date(engine_expiry) or _norm_date(engine_expiry.group(1))
            conf["expiry_date"] = "high"
        elif labeled["expiry"]:
            got = _group_date(labeled["expiry"])
            if got:
                out["expiry_date"] = got
                conf["expiry_date"] = "medium"
        elif labeled["review"]:
            got = _group_date(labeled["review"])
            if got:
                out["expiry_date"] = got
                conf["expiry_date"] = "medium"
        elif len(all_dates) > 1:
            out["expiry_date"] = all_dates[-1]
            conf["expiry_date"] = "low"
        elif all_dates:
            out["expiry_date"] = all_dates[0]
            conf["expiry_date"] = "low"

    if "Review date" in fields and labeled["review"]:
        got = _group_date(labeled["review"])
        if got:
            out["Review date"] = got
            conf["Review date"] = "medium"

    # BAFE / scheme registration identifiers (organisation ID or scheme cert no.)
    bafe_org = re.search(
        r"(?:BAFE\s*)?(?:Registered\s*)?Organisation\s*ID\s*[:#]?\s*(\d{4,})",
        text,
        re.I,
    )
    bafe_scheme = re.search(
        r"(?:BAFE\s*)?(?:SP\d+\s*)?(?:Scheme\s*)?Registration(?:/Certificate)?\s*"
        r"(?:Certificate\s*)?(?:No\.?|Number|#)?\s*[:#]?\s*(\d{4,})",
        text,
        re.I,
    )
    bafe_reg = re.search(
        r"BAFE\s*(?:registration|reg(?:istration)?\.?|cert(?:ificate)?)\s*"
        r"(?:no\.?|number|#)?\s*[:#]?\s*(\d{4,})",
        text,
        re.I,
    )
    if bafe_org:
        out["inspector_accreditation_number"] = bafe_org.group(1).strip()
        conf["inspector_accreditation_number"] = "high"
        out["BAFE Registered Organisation ID"] = bafe_org.group(1).strip()
        conf["BAFE Registered Organisation ID"] = "high"
    if bafe_scheme or bafe_reg:
        num = (bafe_scheme or bafe_reg).group(1).strip()
        out.setdefault("certificate_number", num)
        conf.setdefault("certificate_number", "high")
        out["BAFE registration no."] = num
        conf["BAFE registration no."] = "high"
        if not out.get("inspector_accreditation_number"):
            out["inspector_accreditation_number"] = num
            conf["inspector_accreditation_number"] = "high"

    # EPC band and score (B5). Looked for on every certificate type: the key is only kept
    # for EPC-type packs downstream, and a fire-alarm certificate never says "Asset rating".
    from .epc_rating import _BAND_RE as _epc_band_re  # local: avoids a cycle at import
    m_band = _epc_band_re.search(text)
    if m_band and "energy_rating" not in out:
        out["energy_rating"] = m_band.group(1).upper()
        conf["energy_rating"] = "high" if m_band.group(2) else "medium"
        if m_band.group(2):
            out["energy_score"] = int(m_band.group(2))
            conf["energy_score"] = "high"

    if "certificate_number" in fields and "certificate_number" not in out:
        typed = re.search(
            r"\b((?:FRA|EICR|PAS|LGSR|L8|FAS|AOV|EWS|FRAEW|BAFE|BPCA)[-/]?\d{3,})\b",
            text,
            re.I,
        )
        # BPCA / trade-body membership numbers e.g. "Membership number: M15/ 035"
        membership = re.search(
            r"(?:membership\s*(?:number|no\.?|num)|bpca\s*member\s*(?:no\.?|number)|"
            r"member\s*(?:number|no\.?))\s*[:.]?\s*"
            r"([A-Z]?\d{1,4}\s*/\s*\d{2,6}|[A-Z]{2,10}[-/]?\d{3,})",
            text,
            re.I,
        )
        if engine_cert:
            out["certificate_number"] = engine_cert.group(1).strip()
            conf["certificate_number"] = "high"
        elif membership:
            num = re.sub(r"\s*/\s*", "/", membership.group(1).strip())
            num = re.sub(r"\s+", "", num)
            out["certificate_number"] = num
            conf["certificate_number"] = "high"
            if "BPCA member no." in fields:
                out["BPCA member no."] = num
                conf["BPCA member no."] = "high"
        elif typed:
            out["certificate_number"] = typed.group(1).strip().upper()
            conf["certificate_number"] = "high"
        else:
            cert_re = re.compile(
                r"(?:certificate|cert|licence|license)\s*(?:ref(?:erence)?|no|number|#)?"
                r"\s*[:.]?\s*([A-Z]{2,10}[-/]?\d{3,}|\d{4,}[A-Z0-9\-/]*)",
                re.I,
            )
            m = cert_re.search(text)
            # Ignore bare BAFE/company registration digits (e.g. "reg. 3421")
            if m and not re.search(
                r"\b(?:BAFE|SP\d+|England\s*&\s*Wales|Companies\s*House)\b.{0,40}"
                + re.escape(m.group(1)),
                text,
                re.I | re.S,
            ):
                out["certificate_number"] = m.group(1).strip()
                conf["certificate_number"] = "medium"

    # Company / member name (BPCA layout: number then company then "is a Full Member")
    company = None
    m_bpca = re.search(
        r"(?:membership\s*(?:number|no\.?)|bpca\s*member\s*(?:no\.?|number))"
        r"\s*:?\s*[A-Z0-9/\s]{0,20}\s*"
        r"\n+\s*([A-Z][A-Za-z0-9 &.'\-]{2,80}?)\s*\n+\s*is a\s+(?:Full\s+)?Member",
        text,
        re.I,
    )
    if m_bpca:
        company = m_bpca.group(1).strip()
    else:
        m_certify = re.search(
            r"this is to certify that\s+(?:.*?\n){0,4}\s*"
            r"([A-Z][A-Za-z0-9 &.'\-]{2,80}?)\s*\n+\s*is a\s+(?:Full\s+)?Member",
            text,
            re.I,
        )
        if m_certify:
            cand = m_certify.group(1).strip()
            if not re.search(r"membership|number|member\s*no", cand, re.I):
                company = cand
    if company:
        out["Company name"] = company
        conf["Company name"] = "high"
        for k in ("company_name", "vendor_name", "Contractor", "Business name"):
            if k in fields:
                out[k] = company
                conf[k] = "high"

    for result in ("Pass", "Fail", "Advisory", "Satisfactory", "Unsatisfactory"):
        if re.search(rf"\b{result}\b", text, re.I) and (
            "result" in fields or result in fields or "Pass" in fields or "Fail" in fields
        ):
            out["result"] = result
            conf["result"] = "low"
            break
    # Fire Door / EICR: C1 danger present / Refer / immediate action → Fail
    want_result = (
        "result" in fields
        or "Fail" in fields
        or "Pass" in fields
        or "Refer" in fields
        or "Pass/Refer/Fail" in fields
    )
    if "result" not in out and want_result:
        if re.search(
            r"\bc1\b|danger\s+present|immediate\s+action|"
            r"pass\s*/\s*refer\s*/\s*fail\s*[:|]?\s*.*\b(?:c1|fail|refer)\b",
            text,
            re.I,
        ):
            out["result"] = "Fail"
            conf["result"] = "high"
            m = re.search(
                r"(?:pass\s*/\s*refer\s*/\s*fail|result|overall)\s*[:|]?\s*"
                r"([^\n]{5,120})",
                text,
                re.I,
            )
            if m:
                out["defects_found"] = m.group(1).strip()
                conf["defects_found"] = "medium"

    name_re = re.compile(
        r"(?:assessor|inspector|engineer|surveyor)\s*(?:name(?:\s+and\s+qualification)?)?"
        r"\s*[:|\t]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
        re.I,
    )
    nm = name_re.search(text)
    if nm:
        person = nm.group(1).strip().splitlines()[0].strip()
        # Drop trailing label bleed (e.g. "James Okafor Review date")
        person = re.split(
            r"\s+(?:Review|Date|Scope|Signature|Qualification)\b",
            person,
            maxsplit=1,
        )[0].strip()
        if "inspector_name" in fields:
            out["inspector_name"] = person
            conf["inspector_name"] = "medium"
        if "Assessor name and qualification" in fields:
            out["Assessor name and qualification"] = person
            conf["Assessor name and qualification"] = "medium"

    return out, conf


async def _claude_extract(
    *,
    source_text: str,
    certificate_type_code: str,
    type_name: str,
    fields: list[str],
    pdf_base64: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        extra_keys = [
            "inspector_accreditation_number",
            "certificate_number",
            "company_name",
            "vendor_name",
            "insurer_name",
            "BAFE registration no.",
            "BAFE Registered Organisation ID",
            # Sub-national grouping. These must be listed here, not only described in the
            # prompt: the result loop below keeps only keys present in `want`, so a state
            # the model correctly returned would otherwise be silently dropped.
            "state",
            "region",
            # EPC asset rating: the band letter and its score, as two keys.
            "energy_rating",
            "energy_score",
        ]
        want = list(dict.fromkeys([*fields, *extra_keys]))
        prompt = (
            f"Extract fields for UK compliance certificate type {certificate_type_code} "
            f"({type_name}). The PDF may be a scan/image. Read the FULL document.\n"
            f"Return ONLY JSON with keys among {want} plus per_field_confidence "
            f"(high|medium|low). Prefer organisation / registration / membership "
            f"IDs for inspector_accreditation_number (e.g. BAFE Organisation ID).\n"
            f"For insurance certificates (Employers'/Public Liability), `insurer_name` is "
            f"the FCA-authorised INSURANCE COMPANY / underwriter that provides the cover "
            f"(often shown as the 'Authorised Insurer' or in the 'regulated by the "
            f"Financial Conduct Authority' footer) — this is DIFFERENT from `company_name` "
            f"/ `vendor_name`, which is the policyholder being insured.\n"
            f"`energy_rating` is an EPC's asset-rating BAND, a single letter A-G, and "
            f"`energy_score` the number beside it (e.g. 'C 63' -> 'C', 63); both null unless "
            f"the document is an energy certificate.\n"
            f"`state` is the first-level division of the address on the document, spelled "
            f"out IN FULL and never abbreviated — for the UK one of England, Scotland, "
            f"Wales or Northern Ireland (infer from the address, city or postcode area); "
            f"for the US the full state name ('Washington', not 'WA'); for the UAE the "
            f"emirate. `region` is the city or county below it ('London', 'Norfolk'). "
            f"Leave either null if the document does not state it — never guess from the "
            f"issuing body's own address.\n"
            f"Use ISO dates YYYY-MM-DD when possible."
        )
        if source_text and source_text.strip():
            # 14,000 characters was roughly 3,500 tokens against a 200k-token window — it cut
            # off any certificate longer than a few pages, and an asbestos survey or an EICR
            # with a schedule of defects is routinely longer than that. The PDF, when one is
            # attached, competes for the same window, so the text gets the smaller share.
            budget = 60_000 if pdf_base64 else 200_000
            body = source_text[:budget]
            if len(source_text) > budget:
                log.warning(
                    "compliance.source_text_trimmed",
                    chars_total=len(source_text),
                    chars_sent=len(body),
                    chars_dropped=len(source_text) - len(body),
                )
            prompt += f"\n\nSOURCE TEXT (may be OCR/partial):\n{body}"

        content: list[dict[str, Any]] = []
        if pdf_base64 and len(pdf_base64) < 28_000_000:
            content.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_base64,
                    },
                }
            )
            # PDF/vision extraction. Must be a model available to this API key —
            # "claude-sonnet-4-20250514" 404s here, silently emptying every extraction
            # (classification still worked because it uses the Haiku model below).
            model = "claude-haiku-4-5-20251001"
        else:
            model = "claude-haiku-4-5-20251001"
        content.append({"type": "text", "text": prompt})

        resp = await client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text if resp.content else "{}"
        raw = re.sub(r"^```json\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
        data = json.loads(raw)
        conf = data.pop("per_field_confidence", {}) or {}

        def _flatten(v: Any) -> Any:
            # Claude sometimes returns a field as {"value": X, "confidence": Y}
            # or a list; downstream date/string handling needs a scalar, so unwrap it
            # (otherwise a date dict reaches fromisoformat -> "Invalid isoformat string").
            if isinstance(v, dict):
                return v.get("value", v.get("val"))
            if isinstance(v, list):
                joined = ", ".join(str(x).strip() for x in v if x not in (None, ""))
                return joined or None
            return v

        fields_out: dict[str, Any] = {}
        conf_out: dict[str, str] = {}
        for k in want:
            rawv = data.get(k)
            val = _flatten(rawv)
            if val is None or (isinstance(val, str) and not val.strip()):
                continue
            fields_out[k] = val
            conf_out[k] = (
                rawv.get("confidence")
                if isinstance(rawv, dict) and rawv.get("confidence")
                else conf.get(k, "medium")
            )
        return fields_out, conf_out
    except Exception as exc:  # noqa: BLE001
        log.warning("extract.claude_failed", error=str(exc))
        return _heuristic_extract(source_text or "", fields)

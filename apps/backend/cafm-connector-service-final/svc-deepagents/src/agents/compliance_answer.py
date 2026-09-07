"""Deterministic compliance chat answers built from tool JSON (not LLM guesses).

Produces detailed, PM-readable markdown (vendor, accreditation, status, expiry, etc.)
so /ai matches /compliance — never count-only when rows are available.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _as_dict(v: Any) -> dict[str, Any]:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _tool_payload(tc: dict[str, Any]) -> dict[str, Any]:
    out = _as_dict(tc.get("output"))
    nested = _as_dict(out.get("result"))
    return nested if nested else out


def _n(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _certs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    arr = payload.get("certificates")
    return [r for r in arr if isinstance(r, dict)] if isinstance(arr, list) else []


_COMPANY_NAME_STOPWORDS = {
    "company",
    "holdings",
    "limited",
    "ltd",
    "plc",
    "services",
    "solutions",
    "the",
}


def _query_mentions_vendor(user_message: str, row: dict[str, Any]) -> bool:
    """Match a named company in the question without treating generic suffixes as identity."""
    vendor_name = str(row.get("vendor_name") or "").lower()
    if not vendor_name:
        return False
    query_tokens = set(re.findall(r"[a-z0-9]+", user_message.lower()))
    name_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", vendor_name)
        if len(token) >= 3 and token not in _COMPANY_NAME_STOPWORDS
    }
    return bool(query_tokens & name_tokens)


def _vendor_count_predicate(user_message: str) -> tuple[int, str] | None:
    """Parse strict natural-language vendor certificate-count comparisons."""
    msg = user_message.lower()
    patterns = (
        (r"\bmore\s+than\s+(\d+)\b", "gt"),
        (r"\bover\s+(\d+)\b", "gt"),
        (r"\bat\s+least\s+(\d+)\b", "gte"),
        (r"\b(\d+)\s+or\s+more\b", "gte"),
        (r"\bexactly\s+(\d+)\b", "eq"),
    )
    if "vendor" not in msg or not re.search(r"\b(certificat|accreditation)", msg):
        return None
    for pattern, comparison in patterns:
        match = re.search(pattern, msg)
        if match:
            return int(match.group(1)), comparison
    return None


def _aggregate_vendor_rows(
    rows: list[dict[str, Any]],
    *,
    min_count: int,
    comparison: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        vendor_name = str(row.get("vendor_name") or "").strip()
        if not vendor_name:
            continue
        key = str(row.get("vendor_id") or "").strip() or vendor_name.casefold()
        entry = grouped.setdefault(
            key,
            {
                "vendor_name": vendor_name,
                "vendor_id": row.get("vendor_id"),
                "certificates_by_id": {},
            },
        )
        cert_id = str(
            row.get("id")
            or row.get("certificate_number")
            or row.get("certificate_ref")
            or ""
        ).strip()
        if cert_id:
            entry["certificates_by_id"][cert_id] = row

    def qualifies(count: int) -> bool:
        if comparison == "gt":
            return count > min_count
        if comparison == "gte":
            return count >= min_count
        return count == min_count

    result = [
        {
            "vendor_id": entry["vendor_id"],
            "vendor_name": entry["vendor_name"],
            "certificate_count": len(entry["certificates_by_id"]),
            "certificates": list(entry["certificates_by_id"].values()),
        }
        for entry in grouped.values()
        if qualifies(len(entry["certificates_by_id"]))
    ]
    return sorted(
        result,
        key=lambda row: (-int(row["certificate_count"]), str(row["vendor_name"]).casefold()),
    )


def _name(row: dict[str, Any]) -> str:
    return str(
        row.get("certificate_type_name")
        or row.get("certificate_type_code")
        or row.get("certificate_number")
        or "Certificate"
    )


def _s(row: dict[str, Any], *keys: str, default: str = "—") -> str:
    for k in keys:
        v = row.get(k)
        if v is None or v == "":
            continue
        return str(v).strip() or default
    return default


def _days_label(row: dict[str, Any]) -> str:
    expiry = _s(row, "expiry_date")
    days = row.get("days_to_expiry")
    if isinstance(days, int):
        if days < 0:
            return f"{expiry} ({abs(days)}d overdue)" if expiry != "—" else f"{abs(days)}d overdue"
        return f"{expiry} ({days}d left)" if expiry != "—" else f"{days}d left"
    return expiry


def _forensics_line(row: dict[str, Any]) -> str | None:
    """'- Document forensics: PASS (risk 27/100)' from the row's stored forensics."""
    meta = row.get("raw_metadata") or {}
    f = meta.get("forensics") if isinstance(meta.get("forensics"), dict) else {}
    verdict = str(
        (f or {}).get("verdict") or meta.get("forensics_verdict") or ""
    ).strip().upper()
    risk = (f or {}).get("risk_score")
    if risk is None:
        risk = meta.get("forensics_risk_score")
    if not verdict and risk is None:
        return None
    if verdict in {"PASS", "OK", "CLEAR", "LOW", "LOW_RISK"}:
        label = "PASS"
    elif verdict:
        label = "FAIL" if verdict in {"FAIL", "HIGH", "HIGH_RISK", "BLOCK"} else verdict.title()
    else:
        label = "—"
    risk_txt = f" (risk {int(risk)}/100)" if isinstance(risk, (int, float)) else ""
    return f"- Document forensics: {label}{risk_txt}"


def _verification_line(row: dict[str, Any]) -> str | None:
    """'- Accreditation verification status: `verified`' from stored §8 verification."""
    meta = row.get("raw_metadata") or {}
    v = meta.get("verification") if isinstance(meta.get("verification"), dict) else {}
    status = str(
        (v or {}).get("status")
        or ("verified" if (v or {}).get("verified") is True else "")
        or ""
    ).strip()
    if not status:
        return None
    channel = str((v or {}).get("channel") or "").strip()
    chan_txt = f" · channel: `{channel}`" if channel else ""
    return f"- Accreditation verification status: `{status}`{chan_txt}"


def _format_building_row(row: dict[str, Any], idx: int) -> str:
    lines = [
        f"**{idx}. {_name(row)}**",
        "- Type: Building",
        f"- Vendor / company: {_s(row, 'vendor_name', 'building_name', 'issuing_body')}",
        f"- Certificate / membership #: `{_s(row, 'certificate_number', 'certificate_ref')}`",
        f"- Status: **{_s(row, 'status')}** · {_days_label(row)}",
        f"- Issue / membership start: {_s(row, 'issue_date')}",
        f"- Expiry / valid until: {_s(row, 'expiry_date')}",
    ]
    inspector = _s(row, "inspector_name", default="")
    if inspector and inspector != "—":
        accred = _s(row, "inspector_accreditation_number", default="")
        suffix = f" (accreditation `{accred}`)" if accred and accred != "—" else ""
        lines.append(f"- Inspector / assessor: {inspector}{suffix}")
    fl = _forensics_line(row)
    if fl:
        lines.append(fl)
    vl = _verification_line(row)
    if vl:
        lines.append(vl)
    if row.get("draft"):
        lines.append("- Flag: **DRAFT** (awaiting PM confirmation)")
    return "\n".join(lines)


def _format_vendor_row(row: dict[str, Any], idx: int) -> str:
    risk = _s(row, "risk_badge", "risk_level")
    block = _s(row, "vendor_block_state", "block_reason")
    status_line = f"- Status: **{_s(row, 'status')}** · {_days_label(row)}"
    if risk and risk != "—":
        status_line += f" · Risk: **{risk}**"
    lines = [
        f"**{idx}. {_name(row)}**",
        "- Type: Vendor",
        f"- Vendor / company: {_s(row, 'vendor_name', default='Unknown vendor')}",
        f"- Certificate / membership #: `{_s(row, 'certificate_number', 'certificate_ref', 'inspector_accreditation_number')}`",
        status_line,
        f"- Issue / membership start: {_s(row, 'issue_date')}",
        f"- Expiry / valid until: {_s(row, 'expiry_date')}",
    ]
    fl = _forensics_line(row)
    if fl:
        lines.append(fl)
    vl = _verification_line(row)
    if vl:
        lines.append(vl)
    is_draft = bool(row.get("draft")) or bool(
        _as_dict(row.get("raw_metadata")).get("requires_pm_confirmation")
    )
    if str(row.get("vendor_block_state") or "").lower() == "blocked" or risk.lower() == "blocked":
        reason = _s(row, "block_reason", default="")
        reason_suffix = f" ({reason})" if reason and reason != "—" else ""
        if is_draft:
            # The block rests on an unconfirmed draft extract — report it as provisional, not a
            # confirmed "cannot lawfully perform" state, so it reads distinctly from a live block.
            lines.append(
                "- **Blocked (provisional)** — based on a draft accreditation awaiting PM "
                "confirmation; confirm it in Approvals / Saved Space to make it a live block"
                + reason_suffix
            )
        else:
            lines.append(
                "- **Blocked** — cannot perform this regulated work" + reason_suffix
            )
    elif block and block not in {"—", "Clear", "clear"}:
        lines.append(f"- Block state: {block}")
    if row.get("draft"):
        lines.append("- Flag: **DRAFT** (awaiting PM confirmation)")
    return "\n".join(lines)


def _detail_list(
    rows: list[dict[str, Any]],
    *,
    kind: str,
    limit: int = 20,
) -> str:
    if not rows:
        return ""
    fmt = _format_vendor_row if kind == "vendor" else _format_building_row
    blocks = [fmt(r, i) for i, r in enumerate(rows[:limit], 1)]
    more = ""
    if len(rows) > limit:
        more = f"\n\n_…and **{len(rows) - limit}** more — open [/compliance](/compliance) for the full list._"
    return "\n\n".join(blocks) + more


def _cert_ref(row: dict[str, Any]) -> str | None:
    ref = _s(row, "certificate_number", "certificate_ref", default="")
    return ref if ref and ref != "—" else None


def _is_blocked(row: dict[str, Any]) -> bool:
    return (
        str(row.get("vendor_block_state") or "").lower() == "blocked"
        or str(row.get("risk_badge") or row.get("risk_level") or "").lower() == "blocked"
    )


def _is_lapsed(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").lower()
    days = row.get("days_to_expiry")
    return status in {"lapsed", "expired"} or (isinstance(days, int) and days < 0)


def _is_at_risk(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").lower()
    if status in {
        "expiring soon",
        "due for renewal",
        "overdue",
        "critical",
    }:
        return True
    days = row.get("days_to_expiry")
    return isinstance(days, int) and 0 <= days <= 60


def _forensics(row: dict[str, Any]) -> tuple[str, str, Any]:
    """(verdict, ccc_verdict, risk_score) from the top-level fields or raw_metadata.forensics."""
    v = str(row.get("forensics_verdict") or "").lower()
    ccc = str(row.get("forensics_ccc_verdict") or "").lower()
    risk = row.get("forensics_risk_score")
    if not v or risk is None:
        f = _as_dict(row.get("raw_metadata")).get("forensics") or {}
        v = v or str(f.get("verdict") or "").lower()
        ccc = ccc or str(f.get("ccc_verdict") or "").lower()
        if risk is None:
            risk = f.get("risk_score")
    return v, ccc, risk


def _is_forged(row: dict[str, Any]) -> bool:
    """A certificate whose document forensics flags a forgery / edit / authenticity concern."""
    v, ccc, risk = _forensics(row)
    return (
        v in {"fail", "review", "suspect", "edited"}
        or ccc in {"edited", "suspect", "forged", "tampered"}
        or (isinstance(risk, (int, float)) and risk >= 30)
    )


def _forensics_reasons(row: dict[str, Any]) -> list[str]:
    fnd = row.get("forensics_findings")
    if isinstance(fnd, list) and fnd:
        return [str(x) for x in fnd if x]
    f = _as_dict(row.get("raw_metadata")).get("forensics") or {}
    return [
        str(fi.get("message"))
        for fi in (f.get("findings") or [])
        if isinstance(fi, dict) and fi.get("message")
    ]


def _format_forensics_row(row: dict[str, Any], idx: int) -> str:
    v, ccc, risk = _forensics(row)
    band = (
        "HIGH RISK" if (v == "fail" or (isinstance(risk, (int, float)) and risk >= 70))
        else "REVIEW" if v in {"review", "suspect"} or (isinstance(risk, (int, float)) and risk >= 30)
        else "OK"
    )
    who = _s(row, "vendor_name", "building_name", default="—")
    lines = [
        f"**{idx}. {_name(row)}** — {who}",
        f"- Certificate #: `{_s(row, 'certificate_number', 'certificate_ref')}`",
        f"- Verdict: **{(v or '—').upper()}**"
        + (f" · edit signal: {ccc}" if ccc else "")
        + (f" · risk **{risk}/100**" if risk is not None else "")
        + f" · **{band}**",
    ]
    reasons = _forensics_reasons(row)
    for r in reasons[:5]:
        lines.append(f"    - {r}")
    return "\n".join(lines)


def _forgery_answer(
    building_rows: list[dict[str, Any]],
    vendor_rows: list[dict[str, Any]],
) -> str:
    b_flag = [r for r in building_rows if _is_forged(r)]
    v_flag = [r for r in vendor_rows if _is_forged(r)]
    if not b_flag and not v_flag:
        return (
            "No certificates currently show a document-authenticity concern — every certificate "
            "passed document forensics (genuine)."
        )
    parts: list[str] = [
        f"**{len(b_flag) + len(v_flag)}** certificate(s) flagged by document forensics "
        "(possible forged / edited / suspect authenticity) — shown separately by level:",
    ]
    parts.append(
        f"\n### 🏢 Building level — {len(b_flag)} flagged"
        + ("\n\n" + "\n\n".join(_format_forensics_row(r, i) for i, r in enumerate(b_flag, 1))
           if b_flag else "\n\n_None._")
    )
    parts.append(
        f"\n### 👷 Vendor level — {len(v_flag)} flagged"
        + ("\n\n" + "\n\n".join(_format_forensics_row(r, i) for i, r in enumerate(v_flag, 1))
           if v_flag else "\n\n_None._")
    )
    parts.append(
        "\n### What to do next\n\n"
        "- These are **soft authenticity gates** — the PM retains override authority; nothing is "
        "auto-rejected.\n"
        "- For **HIGH RISK / edited (FAIL)** certificates, do not treat as valid evidence: request "
        "a fresh original from the issuer and re-verify against the issuing body before confirming.\n"
        "- Open the certificate's Verify-now register link to confirm authenticity."
    )
    return "\n".join(parts)


def _is_open_remedial(row: dict[str, Any]) -> bool:
    rem = str(row.get("remedial_status") or "").strip().lower()
    return bool(rem) and rem not in {"closed", "—", "-"}


def _try_saying(refs: list[str], *, verb: str = "Draft a renewal email for") -> list[str]:
    out: list[str] = []
    for ref in refs[:3]:
        out.append(f'Ask: “{verb} {ref}”')
    return out


def _proactive_next_steps(
    *,
    kind: str,
    rows: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
    empty_ok_message: str | None = None,
) -> str:
    """PM-facing next actions — always append when answering compliance facts."""
    rows = rows or []
    lines: list[str] = ["", "### What to do next", ""]

    blocked = [r for r in rows if _is_blocked(r)]
    lapsed = [r for r in rows if _is_lapsed(r)]
    at_risk = [r for r in rows if _is_at_risk(r) and not _is_lapsed(r)]
    remedial = [r for r in rows if _is_open_remedial(r)]
    drafts = [r for r in rows if r.get("draft")]
    refs_lapsed = [r for r in (_cert_ref(x) for x in lapsed) if r]
    refs_risk = [r for r in (_cert_ref(x) for x in at_risk) if r]
    refs_blocked = [r for r in (_cert_ref(x) for x in blocked) if r]

    if empty_ok_message and not rows:
        lines.append(empty_ok_message)
        lines.append(
            "- Keep monitoring via [/compliance](/compliance) and Approvals for alert-ladder "
            "(Expiring Soon → Due → Overdue → Critical)."
        )
        return "\n".join(lines)

    if kind == "building":
        if lapsed:
            lines.append("**Lapsed / expired building certificates (statutory — act now)**")
            lines.append(
                "1. **Book a renewal inspection** with a contractor that holds the required "
                "accreditation for that trade (see Country Pack)."
            )
            lines.append(
                "2. **Draft a renewal booking email** in chat (queues an Approvals "
                "`booking_request` — suggested 5 business-day window; **no work order** is created)."
            )
            for tip in _try_saying(refs_lapsed):
                lines.append(f"   - {tip}")
            lines.append(
                "3. After the new certificate arrives, **upload it** (or confirm the extract) so "
                "status returns to Current / Compliant."
            )
            lines.append(
                "4. Open remedials stay with the PM via existing FM channels — close "
                "`remedial_status` when work is done."
            )
            lines.append("")
        if at_risk:
            lines.append("**Near expiry / at-risk (renew before it lapses)**")
            lines.append(
                "1. Treat Expiring Soon / Due for Renewal / Overdue / Critical as a "
                "**renewal window** — book before the certificate becomes Lapsed."
            )
            lines.append(
                "2. Draft the booking early so Approvals can chase on time:"
            )
            for tip in _try_saying(refs_risk or refs_lapsed):
                lines.append(f"   - {tip}")
            lines.append(
                "3. Prefer Critical / shortest days-to-expiry first."
            )
            lines.append("")
    elif kind == "vendor":
        if blocked or lapsed:
            lines.append("**Blocked / lapsed vendor accreditations**")
            lines.append(
                "1. **Do not assign** that vendor regulated work of this trade until the "
                "accreditation is Current again (Blocked = cannot lawfully perform)."
            )
            lines.append(
                "2. **Chase renewal**: draft a vendor renewal email (queues Approvals "
                "`vendor_email` — PM sends from the card; nothing is auto-emailed)."
            )
            for tip in _try_saying(refs_blocked or refs_lapsed):
                lines.append(f"   - {tip}")
            lines.append(
                "3. Point the vendor at the accreditation's own **issuing body** — the "
                "certification body (e.g. a UKAS-accredited body for ISO), scheme register, or "
                "insurer/broker for insurance — then **upload the renewed certificate**."
            )
            lines.append(
                "4. Once Current, the Blocked risk badge clears and they can be booked again."
            )
            lines.append("")
        if at_risk and not (blocked or lapsed):
            lines.append("**Vendor accreditation nearing expiry**")
            lines.append(
                "1. Chase renewal **before** it lapses — a lapsed vendor accreditation "
                "auto-blocks regulated work."
            )
            for tip in _try_saying(refs_risk):
                lines.append(f"   - {tip}")
            lines.append("")
    else:  # posture / mixed
        if blocked or lapsed:
            vendorish = [
                r
                for r in blocked + lapsed
                if r.get("vendor_name") or str(r.get("cert_scope") or "") == "Vendor"
            ]
            buildingish = [
                r
                for r in lapsed
                if not r.get("vendor_name") and str(r.get("cert_scope") or "") != "Vendor"
            ]
            if vendorish:
                lines.append("**Blocked / lapsed vendor accreditations**")
                lines.append(
                    "1. Do not assign that regulated work until Current again."
                )
                lines.append(
                    "2. Draft a vendor renewal email (Approvals `vendor_email`) then upload the renewed cert."
                )
                for tip in _try_saying(
                    [r for r in (_cert_ref(x) for x in vendorish) if r]
                ):
                    lines.append(f"   - {tip}")
                lines.append("")
            if buildingish or (lapsed and not vendorish):
                targets = buildingish or lapsed
                lines.append("**Lapsed building certificates**")
                lines.append(
                    "1. Book renewal inspection → draft booking email → upload renewed certificate."
                )
                for tip in _try_saying([r for r in (_cert_ref(x) for x in targets) if r]):
                    lines.append(f"   - {tip}")
                lines.append("")
        if summary:
            bc = _as_dict(summary.get("building_certificates"))
            vc = _as_dict(summary.get("vendor_certificates"))
            rd = _as_dict(summary.get("risk_dashboard"))
            if _n(bc.get("non_compliant")) or _n(bc.get("lapsed")):
                lines.append(
                    f"- **{_n(bc.get('non_compliant') or bc.get('lapsed'))}** lapsed building "
                    "cert(s) → ask “which building certificates are lapsed?” then draft renewal bookings."
                )
            if _n(bc.get("at_risk")):
                lines.append(
                    f"- **{_n(bc.get('at_risk'))}** at-risk building cert(s) → renew inside the "
                    "alert window before they lapse."
                )
            if _n(rd.get("vendors_blocked")):
                lines.append(
                    f"- **{_n(rd.get('vendors_blocked'))}** blocked vendor(s) → ask “which vendors "
                    "are blocked?” then draft renewal chases; do not assign that regulated work."
                )
            if _n(vc.get("at_risk")):
                lines.append(
                    f"- **{_n(vc.get('at_risk'))}** at-risk vendor accreditation(s) → chase before Blocked."
                )
            if (
                not _n(bc.get("non_compliant"))
                and not _n(bc.get("lapsed"))
                and not _n(rd.get("vendors_blocked"))
                and not _n(bc.get("at_risk"))
                and not blocked
                and not lapsed
            ):
                lines.append(
                    "- Portfolio looks clear on non-compliant / blocked — keep the alert ladder "
                    "and Approvals under review."
                )
            lines.append(
                "- Use [/compliance](/compliance) **Renew ✉** on any alert-ladder row, or ask in chat "
                "to draft a renewal email by certificate number."
            )
        elif not blocked and not lapsed and not at_risk:
            lines.append(
                "- Use [/compliance](/compliance) **Renew ✉** or ask chat to draft a renewal "
                "email by certificate number."
            )

    if remedial and kind in {"building", "vendor"}:
        lines.append("**Open remedial findings**")
        lines.append(
            "- Fail / C1 / open remedial items are tracked in Approvals — arrange remedial work "
            "via your FM process (engine does **not** auto-create work orders), then mark remedial Closed."
        )
        lines.append("")

    if drafts and kind in {"building", "vendor"}:
        lines.append("**Draft extracts**")
        lines.append(
            "- Confirm draft certificates in Approvals / Saved Space so they become live records "
            "(draft ≠ a lifecycle status)."
        )
        lines.append("")

    if kind in {"building", "vendor"} and rows and "### What to do next" in "\n".join(lines):
        # Ensure we always have at least generic actions if scenario filters missed
        if len(lines) <= 3:
            lines.append(
                "- Review full detail on [/compliance](/compliance); use **Renew ✉** for "
                "non-Current rows or ask chat to draft a renewal email."
            )

    # Trim trailing blank
    while lines and lines[-1] == "":
        lines.pop()
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _with_guidance(
    body: str,
    *,
    kind: str,
    rows: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
    empty_ok_message: str | None = None,
) -> str:
    guide = _proactive_next_steps(
        kind=kind,
        rows=rows,
        summary=summary,
        empty_ok_message=empty_ok_message,
    )
    return f"{body}{guide}" if guide else body


def _frequency_label(months: Any) -> str:
    """'Annual', '6-monthly', or 'As required' — the interval a PM plans around."""
    try:
        m = int(months)
    except (TypeError, ValueError):
        return "As required"
    if m <= 0:
        return "As required"
    return {
        1: "Monthly", 6: "6-monthly", 12: "Annual", 24: "2-yearly", 36: "3-yearly",
        48: "4-yearly", 60: "5-yearly", 120: "10-yearly",
    }.get(m, f"{m}-monthly")


def _pack_scope_section(types: list[dict[str, Any]], scope: str, heading: str, duty: str) -> str:
    """One statutory scope, grouped by trade. Never truncated.

    The whole list IS the answer to "what is required", so a cap would hide exactly the
    obligations a PM has no certificate for — the ones most worth knowing about. Grouping by
    trade is what keeps 27 rows readable without dropping any of them.
    """
    rows = [t for t in types if str(t.get("certificate_scope") or "") == scope]
    if not rows:
        return ""
    by_trade: dict[str, list[dict[str, Any]]] = {}
    for t in rows:
        by_trade.setdefault(str(t.get("trade_category") or "General"), []).append(t)

    out = [f"### {heading} — {len(rows)} type(s)", "", f"_{duty}_", ""]
    for trade in sorted(by_trade):
        out.append(f"**{trade}**")
        out.append("")
        for t in sorted(by_trade[trade], key=lambda x: str(x.get("certificate_type_code") or "")):
            name = t.get("certificate_type_name") or t.get("certificate_type_code")
            bits = [f"`{t.get('certificate_type_code')}`", _frequency_label(t.get("frequency_months"))]
            reg = str(t.get("regulation_reference") or "").strip()
            if reg:
                bits.append(reg)
            out.append(f"- **{name}** — " + " · ".join(bits))
        out.append("")
    return "\n".join(out).rstrip()


def _country_pack_answer(types: list[dict[str, Any]], trade_bit: str) -> str:
    """The statutory list, split into the two duties it actually contains.

    Building and Vendor are different obligations on different parties — the owner must hold
    the certificate, the contractor must hold the accreditation to issue it. Listing them in
    one flat run reads as a single list of "things we need", which is not what the pack says.
    """
    building = _pack_scope_section(
        types,
        "Building",
        "Building certificates",
        "Held by the building owner / occupier — evidence the building itself is safe.",
    )
    vendor = _pack_scope_section(
        types,
        "Vendor",
        "Vendor accreditations",
        "Held by the contractor — evidence they are competent to carry out that regulated work.",
    )
    n_b = sum(1 for t in types if str(t.get("certificate_scope") or "") == "Building")
    n_v = sum(1 for t in types if str(t.get("certificate_scope") or "") == "Vendor")

    head = [
        f"The Country Pack defines **{len(types)}** certificate type(s){trade_bit} — "
        f"**{n_b}** building and **{n_v}** vendor.",
        "",
    ]
    sections = [x for x in (building, vendor) if x]
    if not sections:
        # Neither scope matched (a trade filter with no types) — say so rather than
        # returning a heading with nothing under it.
        return (
            f"The Country Pack defines no certificate types{trade_bit}. "
            "Check the trade name, or ask without a trade filter for the full pack."
        )
    body = "\n\n".join(sections)
    # Blank lines matter here: a "---" placed directly under a text line is parsed as a
    # setext heading, which silently turns the last certificate into an <h2>.
    tail = [
        "",
        "---",
        "",
        "_Source: Country Pack taxonomy — what the regulations require, independent of the "
        "certificates on file. A type with nothing on record is still required._",
        "",
        "**Applicability varies by building.** Gas types apply only where there is a gas "
        "appliance, LOLER only where there is a lift, and Building Safety Act duties only to "
        "higher-risk buildings. This is the universe of obligations, not a list every "
        "building owes.",
        "",
        "### What to do next",
        "",
        "- Cross-check this list against certificates on file — ask "
        "\u201cwhich building certificates are lapsed?\u201d and \u201cwhich vendors are "
        "blocked?\u201d",
        "- For a per-building gap view (types on file against the pack), open "
        "[/compliance](/compliance).",
        "- A missing building type needs a renewal inspection booking; a missing vendor "
        "accreditation needs a vendor chase before that trade is assigned.",
    ]
    return "\n".join(head) + "\n" + body + "\n" + "\n".join(tail)


def build_deterministic_compliance_answer(
    user_message: str,
    tool_calls: list[dict[str, Any]] | None,
) -> str | None:
    """Return a detailed factual markdown answer from tool outputs, or None."""
    if not tool_calls:
        return None

    msg = (user_message or "").lower()
    summary: dict[str, Any] | None = None
    building_rows: list[dict[str, Any]] = []
    vendor_rows: list[dict[str, Any]] = []
    count_payload: dict[str, Any] | None = None
    tools_used: list[str] = []
    country_pack: dict[str, Any] | None = None
    vendor_filter: str | None = None
    vendor_count_payload: dict[str, Any] | None = None
    renewal_payload: dict[str, Any] | None = None

    for tc in tool_calls:
        name = str(tc.get("tool") or "")
        if name.startswith("phase2_engine:"):
            continue
        tool_input = _as_dict(tc.get("input"))
        if name == "list_vendor_accreditations":
            requested_vendor = str(
                tool_input.get("vendor_name") or tool_input.get("vendor_id") or ""
            ).strip()
            if requested_vendor:
                vendor_filter = requested_vendor
        payload = _tool_payload(tc)
        if not payload or payload.get("error"):
            continue
        tools_used.append(name)

        if name == "get_compliance_saved_space_summary" or (
            payload.get("building_certificates") and payload.get("risk_dashboard")
        ):
            summary = payload
        elif name == "list_building_certificates":
            building_rows = _certs(payload)
        elif name == "list_vendor_accreditations":
            vendor_rows = _certs(payload)
        elif name == "list_vendors_by_certificate_count":
            vendor_count_payload = payload
        elif name == "draft_certificate_renewal":
            renewal_payload = payload
        elif name == "count_compliance_certificates":
            count_payload = payload
        elif name == "list_country_pack":
            country_pack = payload

    # A named-vendor question is entity-scoped even if the model also called the building
    # portfolio tool. Never leak unrelated building rows into that answer.
    matching_vendor_rows = [
        row for row in vendor_rows if _query_mentions_vendor(user_message, row)
    ]
    vendor_targeted = bool(vendor_filter or matching_vendor_rows)
    if vendor_filter:
        target = vendor_filter.casefold()
        vendor_rows = [
            row
            for row in vendor_rows
            if target in str(row.get("vendor_name") or "").casefold()
            or str(row.get("vendor_name") or "").casefold() in target
        ]
    elif matching_vendor_rows:
        vendor_rows = matching_vendor_rows
    if vendor_targeted:
        building_rows = []

    wants_blocked = "blocked" in msg
    wants_lapsed = any(w in msg for w in ("lapsed", "expired", "expiry past", "past expiry"))
    wants_current = re.search(r"\bcurrent\b", msg) is not None
    wants_vendor = vendor_targeted or "vendor" in msg or "accreditation" in msg or wants_blocked
    wants_building = not vendor_targeted and (
        "building" in msg
        or (wants_lapsed and not wants_vendor)
        or (wants_current and "building" in msg)
    )
    wants_posture = any(
        w in msg
        for w in (
            "compliant",
            "non-compliant",
            "non compliant",
            "at risk",
            "at-risk",
            "compliance status",
            "overview",
            "summary",
            "who is",
        )
    )
    wants_forged = any(
        w in msg
        for w in (
            "forged",
            "forgery",
            "fake",
            "fraud",
            "tamper",
            "tampered",
            "doctored",
            "altered",
            "manipulat",
            "authenticity",
            "genuine",
            "edited document",
            "forensic",
            "suspect document",
        )
    )
    if wants_forged and (building_rows or vendor_rows):
        return _forgery_answer(building_rows, vendor_rows)

    if renewal_payload is not None:
        drafts = [
            draft
            for draft in (renewal_payload.get("email_drafts") or [])
            if isinstance(draft, dict)
        ]
        if not drafts:
            return str(
                renewal_payload.get("message")
                or "The renewal email could not be verified from the tool response."
            )
        requested_number = ""
        for tc in tool_calls:
            if str(tc.get("tool") or "") == "draft_certificate_renewal":
                requested_number = str(
                    _as_dict(tc.get("input")).get("certificate_number") or ""
                ).strip()
                break
        reference = requested_number or "the selected certificate"
        return (
            f"Renewal email draft created for **{reference}** and queued for PM review. "
            "Verify the recipient and content in the draft card below; no email has been sent."
        )

    vendor_count_request = _vendor_count_predicate(user_message)
    if vendor_count_request:
        min_count, comparison = vendor_count_request
        if vendor_count_payload is not None:
            vendors = [
                row
                for row in (vendor_count_payload.get("vendors") or [])
                if isinstance(row, dict)
            ]
        else:
            # Evaluation fallback: if the agent chose the flat list anyway, rebuild the
            # requested GROUP BY/HAVING result rather than returning individual certificates.
            vendors = _aggregate_vendor_rows(
                vendor_rows,
                min_count=min_count,
                comparison=comparison,
            )
        phrase = {
            "gt": f"more than {min_count}",
            "gte": f"at least {min_count}",
            "eq": f"exactly {min_count}",
        }[comparison]
        if not vendors:
            return f"**0 vendors** hold {phrase} accreditation certificates."
        lines = [
            f"**{len(vendors)} vendor{'s' if len(vendors) != 1 else ''}** "
            f"{'holds' if len(vendors) == 1 else 'hold'} {phrase} accreditation certificates:",
            "",
        ]
        for index, vendor in enumerate(vendors, 1):
            lines.append(
                f"{index}. **{vendor.get('vendor_name') or 'Unknown vendor'}** — "
                f"{_n(vendor.get('certificate_count'))} certificates"
            )
            certificates = [
                row for row in (vendor.get("certificates") or []) if isinstance(row, dict)
            ]
            for certificate in certificates:
                cert_name = (
                    certificate.get("certificate_type_name")
                    or certificate.get("certificate_type_code")
                    or "Certificate"
                )
                cert_number = (
                    certificate.get("certificate_number")
                    or certificate.get("certificate_ref")
                    or "number not recorded"
                )
                status = certificate.get("status") or "Status not recorded"
                expiry = certificate.get("expiry_date") or "expiry not recorded"
                days = certificate.get("days_to_expiry")
                timing = ""
                if isinstance(days, int):
                    timing = (
                        f", {abs(days)} days overdue"
                        if days < 0
                        else f", {days} days remaining"
                    )
                lines.append(
                    f"   - **{cert_name}** · {cert_number} · {status} · "
                    f"expires {expiry}{timing}"
                )
                if cert_number != "number not recorded" and re.search(
                    r"lapsed|expired|critical|overdue|due|expiring|blocked",
                    f"{status} {certificate.get('risk_badge') or ''}",
                    flags=re.IGNORECASE,
                ):
                    lines.append(
                        f'     - Follow-up: ask **"Draft a renewal email for {cert_number}"**'
                    )
        return "\n".join(lines)

    # Attribute count — list every match with detail + renewal guidance
    if count_payload is not None and "count" in count_payload:
        n = _n(count_payload.get("count"))
        matches = [
            m for m in (count_payload.get("matches") or []) if isinstance(m, dict)
        ]
        head = f"**{n}** certificate{'s' if n != 1 else ''} match that filter."
        if not matches:
            return head
        has_vendor = any(m.get("vendor_name") for m in matches)
        kind = "vendor" if has_vendor else "building"
        body = f"{head}\n\n{_detail_list(matches, kind=kind, limit=25)}"
        if "insurance" in msg:
            body += (
                "\n\n### What to do next\n\n"
                "- Insurance-risk flags mean underwriters / insurers may treat the asset or "
                "vendor as higher risk — renew or replace the certificate and clear open remedials.\n"
                "- Draft renewal / booking emails from chat or [/compliance](/compliance) **Renew ✉**.\n"
                "- Confirm any draft extracts so the live record reflects the new evidence."
            )
            return body
        return _with_guidance(body, kind=kind, rows=matches)

    # Taxonomy — still proactive about on-file obligations
    if country_pack is not None:
        types = [t for t in (country_pack.get("types") or []) if isinstance(t, dict)]
        if types:
            trade = country_pack.get("trade_category")
            trade_bit = f" (trade: **{trade}**)" if trade else ""
            if len(types) == 1:
                t0 = types[0]
                scope = t0.get("certificate_scope") or "Country Pack"
                return (
                    f"**{t0.get('certificate_type_name') or t0.get('certificate_type_code')}** "
                    f"is a **{scope}** type{trade_bit}.\n\n"
                    f"- Code: `{t0.get('certificate_type_code')}`\n"
                    f"- Issuing body: {t0.get('issuing_body') or '—'}\n"
                    f"- Interval: {_frequency_label(t0.get('frequency_months'))}\n"
                    f"- Regulation: {t0.get('regulation_reference') or '—'}\n\n"
                    f"_Source: UK Country Pack taxonomy (independent of certificates on file)._\n\n"
                    f"### What to do next\n\n"
                    f"- Ensure a live **{scope}** certificate of this type is on file and Current.\n"
                    f"- If missing / lapsed, upload evidence or draft a renewal booking / vendor chase "
                    f"from chat or [/compliance](/compliance).\n"
                    f"- Ask “which building certificates are lapsed?” or “which vendors are blocked?” "
                    f"to see open actions on the portfolio."
                )
            return _country_pack_answer(types, trade_bit)

    if vendor_targeted:
        target_name = (
            str(vendor_rows[0].get("vendor_name") or "").strip()
            if vendor_rows
            else vendor_filter or "the requested vendor"
        )
        if not vendor_rows:
            return (
                f"No accreditation certificates were found for **{target_name}**. "
                "No unrelated building certificates were included."
            )
        statuses = {str(row.get("status") or "Unknown") for row in vendor_rows}
        drafts = sum(
            1
            for row in vendor_rows
            if bool(row.get("draft"))
            or bool(_as_dict(row.get("raw_metadata")).get("requires_pm_confirmation"))
        )
        status_text = ", ".join(sorted(statuses))
        draft_text = (
            f" {drafts} record{' is' if drafts == 1 else 's are'} still a draft awaiting PM "
            "confirmation."
            if drafts
            else ""
        )
        body = (
            f"### Compliance status — {target_name}\n\n"
            f"**{len(vendor_rows)} accreditation certificate"
            f"{' is' if len(vendor_rows) == 1 else 's are'} on file; lifecycle status: "
            f"{status_text}.**{draft_text}\n\n"
            f"{_detail_list(vendor_rows, kind='vendor')}"
        )
        return _with_guidance(body, kind="vendor", rows=vendor_rows)

    # Blocked vendors — full accreditation detail + chase/renew path.
    # Skip when the query is a broad posture/summary ask (e.g. "summarize vendor
    # compliance … blocked …") so it falls through to the portfolio posture branch
    # and never contradicts the summary card.
    if (
        wants_blocked
        and not wants_posture
        and (vendor_rows or any(t == "list_vendor_accreditations" for t in tools_used))
    ):
        # Every cert carrying a Blocked badge or that is itself lapsed.
        blocked_all = [r for r in vendor_rows if _is_blocked(r) or _is_lapsed(r)]
        # The block is CAUSED by the lapsed accreditation. A vendor's other, still-Current
        # accreditations merely inherit the vendor Blocked badge — they are not themselves the
        # problem, so listing them as "blocked accreditations" is misleading (and inflates the
        # count past the number of blocked vendors). Show the lapsed cause per vendor, and only
        # fall back to a badge-blocked cert for a vendor that has no lapsed cert in this list.
        def _vkey(r: dict[str, Any]) -> str:
            return str(r.get("vendor_name") or r.get("vendor_id") or "").casefold()

        lapsed_causes = [r for r in blocked_all if _is_lapsed(r)]
        vendors_with_cause = {_vkey(r) for r in lapsed_causes}
        fallback_seen: set[str] = set()
        fallback: list[dict[str, Any]] = []
        for r in blocked_all:
            key = _vkey(r)
            if _is_lapsed(r) or key in vendors_with_cause or key in fallback_seen:
                continue
            fallback_seen.add(key)
            fallback.append(r)
        blocked = lapsed_causes + fallback
        if not blocked:
            # The filtered rows found none, but the portfolio summary is the source
            # of truth for the count — never claim "none" when it says otherwise.
            rd = _as_dict(summary.get("risk_dashboard")) if summary else {}
            summary_blocked = _n(rd.get("vendors_blocked"))
            if summary_blocked:
                return _with_guidance(
                    f"**{summary_blocked}** vendor{'s' if summary_blocked != 1 else ''} "
                    f"blocked for regulated work (summary count — ask “list blocked vendor "
                    f"accreditations” for full names, accreditation types, and expiry detail).",
                    kind="posture",
                    summary=summary,
                )
            return _with_guidance(
                "No vendors are currently **blocked** for regulated work.",
                kind="vendor",
                rows=[],
                empty_ok_message=(
                    "- No blocked vendors right now — still watch at-risk accreditations "
                    "so they don’t tip into Blocked."
                ),
            )
        vendor_count = len({_vkey(r) for r in blocked})
        head = (
            f"**{vendor_count}** vendor{'s' if vendor_count != 1 else ''} blocked from "
            "regulated work — each shown with the lapsed accreditation that caused the block:"
        )
        return _with_guidance(
            f"{head}\n\n{_detail_list(blocked, kind='vendor')}",
            kind="vendor",
            rows=blocked,
        )

    # Lapsed / expired building (or vendor if scoped). Broad posture/summary asks
    # fall through to the portfolio posture branch so counts stay consistent.
    if wants_lapsed and not wants_posture:
        # Both levels asked for at once ("expired certificates for buildings and at the vendor
        # level") — render BOTH sections instead of returning after the building list. Filter each
        # portfolio to genuinely lapsed rows so an unfiltered fetch (posture shortcut) is safe too.
        if wants_building and wants_vendor:
            b_lapsed = [r for r in building_rows if _is_lapsed(r)]
            v_lapsed = [r for r in vendor_rows if _is_lapsed(r)]
            b_ran = bool(building_rows) or "list_building_certificates" in tools_used
            v_ran = bool(vendor_rows) or "list_vendor_accreditations" in tools_used
            if b_lapsed or v_lapsed or (b_ran and v_ran):
                sections: list[str] = []
                if b_lapsed:
                    sections.append(
                        f"**Building certificates — {len(b_lapsed)} lapsed (expired)**\n\n"
                        f"{_detail_list(b_lapsed, kind='building')}"
                    )
                elif b_ran:
                    sections.append(
                        "**Building certificates — 0 lapsed (expired) on record.**"
                    )
                if v_lapsed:
                    sections.append(
                        f"**Vendor accreditations — {len(v_lapsed)} lapsed (expired)**\n\n"
                        f"{_detail_list(v_lapsed, kind='vendor')}"
                    )
                elif v_ran:
                    sections.append(
                        "**Vendor accreditations — 0 lapsed (expired) on record.**"
                    )
                if sections:
                    return _with_guidance(
                        "\n\n---\n\n".join(sections),
                        kind="posture",
                        rows=b_lapsed + v_lapsed,
                        summary=summary,
                    )
        if building_rows and (wants_building or not wants_vendor or not vendor_rows):
            head = (
                f"**{len(building_rows)}** lapsed (expired) building certificate"
                f"{'s' if len(building_rows) != 1 else ''}:"
            )
            return _with_guidance(
                f"{head}\n\n{_detail_list(building_rows, kind='building')}",
                kind="building",
                rows=building_rows,
            )
        if vendor_rows and wants_vendor:
            head = (
                f"**{len(vendor_rows)}** lapsed vendor accreditation"
                f"{'s' if len(vendor_rows) != 1 else ''}:"
            )
            return _with_guidance(
                f"{head}\n\n{_detail_list(vendor_rows, kind='vendor')}",
                kind="vendor",
                rows=vendor_rows,
            )
        if any(t == "list_building_certificates" for t in tools_used) and not building_rows:
            if summary:
                bc = _as_dict(summary.get("building_certificates"))
                n = _n(bc.get("lapsed") or bc.get("non_compliant"))
                if n:
                    return _with_guidance(
                        f"Summary shows **{n}** lapsed building certificate"
                        f"{'s' if n != 1 else ''}, but the filtered list returned no rows — "
                        f"open [/compliance](/compliance).",
                        kind="posture",
                        summary=summary,
                    )
            return _with_guidance(
                "There are **0** lapsed (expired) building certificates on record.",
                kind="building",
                rows=[],
                empty_ok_message=(
                    "- Nothing lapsed on file — still review at-risk / Expiring Soon rows "
                    "and book renewals early."
                ),
            )

    if wants_current and not wants_posture and building_rows:
        head = (
            f"**{len(building_rows)}** current building certificate"
            f"{'s' if len(building_rows) != 1 else ''}:"
        )
        body = _with_guidance(
            f"{head}\n\n{_detail_list(building_rows, kind='building')}",
            kind="building",
            rows=building_rows,
        )
        if not any(_is_at_risk(r) or _is_lapsed(r) for r in building_rows):
            body += (
                "\n\n_Current = Compliant for now — still check Expiring Soon / Due for Renewal "
                "and book before they lapse._"
            )
        return body

    # Portfolio posture + attach any listed rows for detail
    if summary and wants_posture:
        bc = _as_dict(summary.get("building_certificates"))
        vc = _as_dict(summary.get("vendor_certificates"))
        rd = _as_dict(summary.get("risk_dashboard"))
        building_only = "building" in msg and "vendor" not in msg
        vendor_only = vendor_targeted or ("vendor" in msg and "building" not in msg)

        parts: list[str] = ["### Compliance posture (live portfolio)", ""]
        if not vendor_only:
            parts.append(
                f"- **Building:** {_n(bc.get('compliant'))} compliant · "
                f"{_n(bc.get('non_compliant'))} non-compliant (lapsed) · "
                f"{_n(bc.get('at_risk'))} at-risk · "
                f"{_n(bc.get('not_on_record'))} not on record"
            )
        if not building_only:
            parts.append(
                f"- **Vendor:** {_n(vc.get('compliant'))} compliant · "
                f"{_n(vc.get('non_compliant'))} non-compliant · "
                f"{_n(vc.get('at_risk'))} at-risk · "
                f"**{_n(rd.get('vendors_blocked'))} blocked**"
            )
        body = "\n".join(parts)
        if building_rows and not vendor_only:
            body += (
                f"\n\n### Building certificates ({len(building_rows)})\n\n"
                + _detail_list(building_rows, kind="building")
            )
        if vendor_rows and not building_only:
            body += (
                f"\n\n### Vendor accreditations ({len(vendor_rows)})\n\n"
                + _detail_list(vendor_rows, kind="vendor")
            )
        elif not building_rows and not vendor_rows:
            body += (
                "\n\n_Ask “which building certificates are lapsed?” or "
                "“which vendors are blocked?” for full names and expiry detail._"
            )
        mixed = building_rows + vendor_rows
        if mixed:
            if building_only:
                return _with_guidance(body, kind="building", rows=building_rows, summary=summary)
            if vendor_only:
                return _with_guidance(body, kind="vendor", rows=vendor_rows, summary=summary)
            return _with_guidance(body, kind="posture", rows=mixed, summary=summary)
        return _with_guidance(body, kind="posture", summary=summary)

    # Generic detailed lists
    if building_rows and not vendor_rows:
        head = (
            f"**{len(building_rows)}** building certificate"
            f"{'s' if len(building_rows) != 1 else ''}:"
        )
        return _with_guidance(
            f"{head}\n\n{_detail_list(building_rows, kind='building')}",
            kind="building",
            rows=building_rows,
        )
    if vendor_rows and not building_rows:
        head = (
            f"**{len(vendor_rows)}** vendor accreditation"
            f"{'s' if len(vendor_rows) != 1 else ''}:"
        )
        return _with_guidance(
            f"{head}\n\n{_detail_list(vendor_rows, kind='vendor')}",
            kind="vendor",
            rows=vendor_rows,
        )
    if building_rows and vendor_rows:
        body = (
            f"### Building certificates ({len(building_rows)})\n\n"
            + _detail_list(building_rows, kind="building")
            + f"\n\n### Vendor accreditations ({len(vendor_rows)})\n\n"
            + _detail_list(vendor_rows, kind="vendor")
        )
        return _with_guidance(
            body, kind="posture", rows=building_rows + vendor_rows, summary=summary
        )

    if summary:
        bc = _as_dict(summary.get("building_certificates"))
        vc = _as_dict(summary.get("vendor_certificates"))
        rd = _as_dict(summary.get("risk_dashboard"))
        body = (
            "### Compliance posture (live portfolio)\n\n"
            f"- **Building:** {_n(bc.get('compliant'))} compliant · "
            f"{_n(bc.get('non_compliant'))} non-compliant · "
            f"{_n(bc.get('at_risk'))} at-risk\n"
            f"- **Vendor:** {_n(vc.get('compliant'))} compliant · "
            f"{_n(vc.get('non_compliant'))} non-compliant · "
            f"{_n(vc.get('at_risk'))} at-risk · "
            f"**{_n(rd.get('vendors_blocked'))} blocked**\n\n"
            "_Ask a filtered list question (e.g. lapsed building certs or blocked vendors) "
            "for names, accreditation types, and expiry dates._"
        )
        return _with_guidance(body, kind="posture", summary=summary)

    return None


def compliance_answer_looks_wrong(answer: str, tool_calls: list[dict[str, Any]] | None) -> bool:
    """Heuristic: LLM said zero/none but tools returned rows or non-zero counts."""
    if not tool_calls or not answer:
        return False
    low = answer.lower()
    zeroish = bool(
        re.search(
            r"\b(0|zero|no)\b.+\b(certificate|lapsed|expired|blocked|compliant)\b"
            r"|\b(no|none)\b.+\b(lapsed|expired|blocked|certificates)\b",
            low,
        )
    )
    if not zeroish:
        return False

    for tc in tool_calls:
        name = str(tc.get("tool") or "")
        payload = _tool_payload(tc)
        if name == "count_compliance_certificates" and _n(payload.get("count")) > 0:
            return True
        if name in {"list_building_certificates", "list_vendor_accreditations"}:
            if len(_certs(payload)) > 0:
                return True
        if payload.get("building_certificates"):
            bc = _as_dict(payload.get("building_certificates"))
            if _n(bc.get("lapsed")) + _n(bc.get("non_compliant")) + _n(bc.get("total")) > 0:
                return True
        rd = _as_dict(payload.get("risk_dashboard"))
        if _n(rd.get("vendors_blocked")) > 0:
            return True
    return False

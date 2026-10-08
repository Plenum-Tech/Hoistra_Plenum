"""One small HTML skeleton every account email renders through.

Table-based layout, inline styles only, no external images or fonts — the shape that
survives Outlook's Word rendering engine and does not trip a spam filter on remote-content
fetches. Every account email used to be a bare block of plain text with a raw link in it;
this is the HTML alternative (shared/approvals.py sends both — see send_platform_email's
html_body parameter), the plain text staying the fallback for a client that cannot render it.
"""
from __future__ import annotations

from html import escape

_INK = "#1a1a1a"
_MUTED = "#6b7280"
_ACCENT = "#ea580c"
_BORDER = "#e5e7eb"


def wrap_html(
    *, heading: str, lines: list[str], cta_label: str | None = None, cta_url: str | None = None,
    code: str | None = None, footnote: str | None = None,
) -> str:
    """``lines`` are paragraphs (already the exact wording the plain-text body uses,
    passed through ``escape`` here — never pass pre-built HTML in). ``code`` renders as a
    single large monospace block, the OTP's one job in the message. ``cta_url`` renders as
    a button; a caller with both a code and a link (there is none today) would get both.
    """
    body_html = "".join(f'<p style="margin:0 0 14px;font-size:15px;line-height:1.55;color:{_INK};">{escape(p)}</p>' for p in lines)
    code_html = ""
    if code:
        code_html = (
            f'<div style="margin:22px 0;padding:16px 20px;background:#f9fafb;border:1px solid {_BORDER};'
            f'border-radius:8px;text-align:center;">'
            f'<span style="font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
            f'font-size:28px;letter-spacing:6px;color:{_INK};font-weight:600;">{escape(code)}</span></div>'
        )
    cta_html = ""
    if cta_label and cta_url:
        safe_url = escape(cta_url, quote=True)
        cta_html = (
            f'<div style="margin:22px 0;"><a href="{safe_url}" '
            f'style="display:inline-block;padding:12px 22px;background:{_ACCENT};color:#ffffff;'
            f'font-size:14px;font-weight:600;text-decoration:none;border-radius:6px;">'
            f'{escape(cta_label)}</a></div>'
            f'<p style="margin:0 0 14px;font-size:12px;line-height:1.5;color:{_MUTED};">'
            f'Or paste this link into your browser:<br>'
            f'<span style="word-break:break-all;">{escape(cta_url)}</span></p>'
        )
    footnote_html = (
        f'<p style="margin:22px 0 0;font-size:12px;line-height:1.5;color:{_MUTED};">{escape(footnote)}</p>'
        if footnote else ""
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border-radius:10px;border:1px solid {_BORDER};overflow:hidden;">
<tr><td style="padding:22px 28px;border-bottom:1px solid {_BORDER};">
<span style="font-size:13px;font-weight:700;letter-spacing:0.04em;color:{_INK};">HOISTRA</span>
</td></tr>
<tr><td style="padding:28px;">
<h1 style="margin:0 0 16px;font-size:19px;line-height:1.3;color:{_INK};">{escape(heading)}</h1>
{body_html}{code_html}{cta_html}{footnote_html}
</td></tr>
<tr><td style="padding:16px 28px;border-top:1px solid {_BORDER};">
<span style="font-size:11px;color:{_MUTED};">Hoistra · this message was sent to the address on the account</span>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""

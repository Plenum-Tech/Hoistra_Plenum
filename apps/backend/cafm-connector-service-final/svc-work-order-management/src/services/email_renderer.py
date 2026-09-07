"""
GPT-powered HTML email renderer.
Asks GPT to produce a complete, styled HTML email for each notification type.
Falls back to a minimal HTML wrapper if the GPT call fails.
"""
import json
from typing import Any, Dict

import httpx

from ..core.logging import get_logger

log = get_logger(__name__)

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

_SYSTEM_PROMPT = """You are an expert HTML email designer for AIMMS — an AI-powered Facilities
Management System used in the UAE. Generate professional, clean HTML emails.

Brand guidelines:
- Primary colour: #0f766e (teal-700)  — use for header background
- Accent colour: #134e4a (teal-900)   — use for footer background
- Text: #1f2937 (dark grey)
- Alert/warning: #b45309 (amber-700)
- Success: #15803d (green-700)
- Danger/reject: #b91c1c (red-700)
- Max width: 600px, centred
- Font: Arial, sans-serif
- No external CSS files or image URLs — inline styles only
- Always include: AIMMS logo placeholder (text "AIMMS" in header), footer with
  "Powered by AIMMS · AI Facilities Management" and current year

Return ONLY the complete HTML document. No markdown fences, no explanation, nothing else."""


_TYPE_PROMPTS: Dict[str, str] = {
    "missing_info": """Generate a polite HTML email asking the requester for missing information
needed to raise their maintenance work order.

Context JSON:
{context}

The email should:
- Address the requester by name (from_name)
- List each missing field as a styled bullet with a friendly label
- Make it easy to understand what information is needed
- Have a warm, helpful tone — not robotic
- Include a clear call-to-action: "Reply to this email with the missing details"
""",

    "wo_created": """Generate a professional HTML work order confirmation email.

Context JSON:
{context}

The email should:
- Confirm the work order has been received and created
- Show a styled info card with: Work Order ID, Asset, Location, Priority (badge), Status: Pending Approval
- Reassure the requester they'll be updated as the WO progresses
- Priority badge colours: HIGH/HIGHEST = red, MEDIUM = amber, LOW = green
""",

    "approval_request": """Generate a professional HTML email requesting work order approval from a facilities manager.

Context JSON:
{context}

The email should:
- Address the approver by approver_name (friendly name, NOT the email address)
- In the summary card, show a line "Approver: {approver_name}" and "Email: {approver_email}"
  and if approver_role is set, show "Role: {approver_role}"
- Show a prominent work order summary card with all details
- If assessment_summary is present, show an "AI Assessment" section with criticality badge,
  estimated duration, required skills, parts needed, and a red safety alert banner if critical_safety is true
- Show two large styled action buttons side by side: green "APPROVE" button and red "REJECT" button
- Below the buttons, explain they must reply to this email with the word "Approved" or "Rejected"
- Make the urgency clear — this is a time-sensitive approval request
""",

    "approval_confirmed": """Generate a professional HTML email confirming a work order has been approved.

Context JSON:
{context}

The email should:
- Congratulate/inform the requester their WO has been approved
- Show a green "APPROVED" status banner prominently
- Show work order details card: WO ID, Asset, Location, Priority, Status: Approved — In Preparation
- If technician is present, show assigned technician name prominently
- If ppm_info has status overdue/due_soon, show a blue info banner with the recommendation
- Warm, reassuring tone
""",

    "technician_assignment": """Generate a professional HTML email assigning a work order to a technician.

Context JSON:
{context}

The email should:
- Address the technician by name
- Show assignment details prominently: WO ID, Asset, Location, Priority badge, Issue description
- If ppm_info has ppm data, show a PPM Schedule Status section with status and recommendation
- Include a clear instruction: log in to AIMMS to view safety requirements and required parts
- Professional, action-oriented tone
""",

    "rejection": """Generate a professional HTML email informing the requester their work order was rejected.

Context JSON:
{context}

The email should:
- Deliver the rejection news respectfully and empathetically
- Show a styled rejection card: WO ID, Asset, Reviewed By (approver_name), Status: Not Approved
- If rejection_notes is present, show it clearly as the reason
- Offer a path forward: contact facility manager if they believe this should be reconsidered
- Sensitive, professional tone — not cold
""",
}


async def render_email_html(
    email_type: str,
    context: Dict[str, Any],
    api_key: str,
    model: str = "gpt-4o-mini",
) -> str:
    """
    Ask GPT to render a complete HTML email for the given type and context.
    Returns the HTML string. Falls back to a plain HTML wrapper on any error.
    """
    if not api_key:
        log.warning("email_renderer.no_api_key", email_type=email_type)
        return _fallback_html(email_type, context)

    type_prompt_template = _TYPE_PROMPTS.get(email_type)
    if not type_prompt_template:
        log.warning("email_renderer.unknown_type", email_type=email_type)
        return _fallback_html(email_type, context)

    user_prompt = type_prompt_template.format(context=json.dumps(context, indent=2, default=str))

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _OPENAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000,
                },
            )
            resp.raise_for_status()
            html = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown fences if the model wrapped anyway
        if html.startswith("```"):
            lines = html.splitlines()
            html = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        log.info("email_renderer.rendered", email_type=email_type, html_len=len(html))
        return html

    except Exception as exc:
        log.warning("email_renderer.failed_fallback", email_type=email_type, error=str(exc))
        return _fallback_html(email_type, context)


def _fallback_html(email_type: str, context: Dict[str, Any]) -> str:
    """Minimal branded HTML wrapper used when GPT call fails."""
    subject_map = {
        "missing_info":           "Additional Information Needed",
        "wo_created":             "Work Order Created",
        "approval_request":       "Approval Required",
        "approval_confirmed":     "Work Order Approved",
        "technician_assignment":  "Work Order Assignment",
        "rejection":              "Work Order Not Approved",
    }
    title = subject_map.get(email_type, "AIMMS Notification")

    rows = "".join(
        f"<tr><td style='padding:4px 8px;color:#6b7280;font-size:13px'>{k}</td>"
        f"<td style='padding:4px 8px;font-size:13px'>{v}</td></tr>"
        for k, v in context.items()
        if v and not isinstance(v, dict)
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>{title}</title></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:32px 16px">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;
                    box-shadow:0 1px 3px rgba(0,0,0,.12)">
        <tr>
          <td style="background:#0f766e;padding:24px 32px">
            <span style="color:#fff;font-size:22px;font-weight:700;
                         letter-spacing:1px">AIMMS</span>
          </td>
        </tr>
        <tr>
          <td style="padding:32px">
            <h2 style="margin:0 0 16px;color:#134e4a;font-size:20px">{title}</h2>
            <table cellpadding="0" cellspacing="0" width="100%"
                   style="background:#f9fafb;border-radius:6px;padding:8px">
              {rows}
            </table>
          </td>
        </tr>
        <tr>
          <td style="background:#134e4a;padding:16px 32px;text-align:center">
            <span style="color:#99f6e4;font-size:12px">
              Powered by AIMMS &middot; AI Facilities Management
            </span>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

"""
BE1-04: Email Parser POC
Mocks an Outlook email and extracts work order fields using OpenAI.
"""
import json
import time
from openai import OpenAI
from typing import Dict, Any, List

from ..core.logging import get_logger

log = get_logger(__name__)

# --- Mocked Outlook email (replace with real Graph API call later) ---
SAMPLE_EMAIL: Dict[str, Any] = {
    "id": "MSG-2026042800001",
    "to": "shashank@plenum-tech.com",
    "from": "shashank@plenum-tech.com",
    "from_name": "Shashank Kanangi",
    "subject": "Urgent - HVAC-301 making grinding noise",
    "body": """Hi Facilities Team,

Asset HVAC-301 located at Building A - Roof Level has been making a loud
grinding noise since this morning and cooling capacity has dropped significantly.
The unit shuts off intermittently.

This needs urgent attention — please send a technician as soon as possible.

Best regards,
Shashank Kanangi
Plenum Tech
Building A - Roof Level
Phone: +971-50-123-4567
""",
    "received_at": "2026-04-29T08:15:00Z",
    "attachments": [],
}

_CLASSIFY_SYSTEM = (
    "You are a facilities management triage assistant. "
    "You always respond with valid JSON only — no markdown, no explanation."
)

_CLASSIFY_TEMPLATE = """Is this email a facilities or maintenance request?

Facilities/maintenance emails report: equipment faults, HVAC issues, plumbing leaks,
electrical problems, lift/elevator faults, cleaning requests, security system issues,
generator servicing, or any physical asset repair/inspection need.

Subject: {subject}
Body snippet: {snippet}

Return ONLY: {{"is_maintenance": true or false, "reason": "one short sentence"}}"""

_SYSTEM = (
    "You are a facilities management assistant. "
    "You always respond with valid JSON only — no markdown, no explanation."
)

_USER_TEMPLATE = """Extract work order information from this facilities management email.

From: {from_addr}
Subject: {subject}
Body:
{body}

Return ONLY valid JSON with these fields:
{{
    "asset": "equipment name or ID mentioned (e.g. HVAC Unit, Lift #2)",
    "location": "building / floor / room (e.g. Tower B, Floor 2, Room 4B)",
    "issue_description": "clear description of the reported problem",
    "priority": "low | medium | high | urgent | critical",
    "request_type": "repair | maintenance | inspection | installation",
    "requester_name": "full name of the person who sent the email",
    "requester_email": "email address of the sender",
    "requester_phone": "phone number if present, else null",
    "notes": "any additional context worth capturing"
}}

Use null for fields that are not present in the email."""


class EmailParser:
    """Parses a raw email dict into structured work order fields using OpenAI."""

    REQUIRED_FIELDS = [
        "asset",
        "location",
        "issue_description",
        "requester_name",
        "requester_email",
    ]

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def classify(self, email: Dict[str, Any]) -> Dict[str, Any]:
        """
        Quick pre-filter: is this email a maintenance/facilities request?
        Uses only subject + first 300 chars of body to keep it cheap and fast.
        Returns {"is_maintenance": bool, "reason": str}.
        Sync — wrap with asyncio.to_thread() when calling from async code.
        """
        subject = email.get("subject", "")
        snippet = (email.get("body", "") or "")[:300].replace("\n", " ")
        log.debug("email_classifier.start", model=self.model, subject=subject)

        t0 = time.monotonic()
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            max_tokens=64,
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user",   "content": _CLASSIFY_TEMPLATE.format(
                    subject=subject, snippet=snippet
                )},
            ],
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        result: Dict[str, Any] = json.loads(response.choices[0].message.content)
        log.info(
            "email_classifier.complete",
            subject=subject,
            is_maintenance=result.get("is_maintenance"),
            reason=result.get("reason"),
            elapsed_ms=elapsed_ms,
            tokens_in=response.usage.prompt_tokens if response.usage else None,
            tokens_out=response.usage.completion_tokens if response.usage else None,
        )
        return result

    def parse(self, email: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract work order fields from an email dict.
        Returns structured dict + list of any missing required fields.
        Sync — wrap with asyncio.to_thread() when calling from async code.
        """
        log.info(
            "email_parser.start",
            model=self.model,
            email_id=email.get("id"),
            subject=email.get("subject"),
            from_addr=email.get("from"),
        )

        user_msg = _USER_TEMPLATE.format(
            from_addr=email.get("from", ""),
            subject=email.get("subject", ""),
            body=email.get("body", ""),
        )

        t0 = time.monotonic()
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            max_tokens=1024,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000)

        extracted: Dict[str, Any] = json.loads(response.choices[0].message.content)

        extracted["source"] = "email"
        extracted["source_reference"] = email.get("id")
        extracted["received_at"] = email.get("received_at")
        extracted["attachments"] = email.get("attachments", [])

        missing = self.missing_fields(extracted)

        log.info(
            "email_parser.complete",
            email_id=email.get("id"),
            model=self.model,
            elapsed_ms=elapsed_ms,
            tokens_in=response.usage.prompt_tokens if response.usage else None,
            tokens_out=response.usage.completion_tokens if response.usage else None,
            asset=extracted.get("asset"),
            priority=extracted.get("priority"),
            ready=len(missing) == 0,
            missing_fields=missing or None,
        )
        return {"data": extracted, "missing_fields": missing, "ready": len(missing) == 0}

    def missing_fields(self, extracted: Dict[str, Any]) -> List[str]:
        return [f for f in self.REQUIRED_FIELDS if not extracted.get(f)]


# ---------------------------------------------------------------------------
# Quick smoke-test — run: python -m src.services.email_parser
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()

    parser = EmailParser(
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    )
    result = parser.parse(SAMPLE_EMAIL)

    print("\n=== Parsed Work Order Data ===")
    print(json.dumps(result["data"], indent=2))

    if result["missing_fields"]:
        print(f"\n  Missing fields: {result['missing_fields']}")
    else:
        print("\n  All required fields present -- ready to create work order")

from typing import Any, Dict


class InputNormalizer:
    """Converts email payloads and PPM schedule dicts into the opening user message
    that starts a conversation with the WO orchestrator."""

    def from_email(self, payload: Dict[str, Any]) -> str:
        sender = payload.get("sender_name") or "Unknown sender"
        email = payload.get("sender_email") or ""
        subject = payload.get("subject") or "No subject"
        body = payload.get("body") or ""
        asset = payload.get("asset") or ""
        location = payload.get("location") or ""

        parts = [f"[Incoming email from {sender}" + (f" <{email}>" if email else "") + "]"]
        parts.append(f"Subject: {subject}")
        if asset:
            parts.append(f"Asset mentioned: {asset}")
        if location:
            parts.append(f"Location mentioned: {location}")
        if body:
            parts.append(f"Message:\n{body.strip()}")
        parts.append("\nPlease process this as a work order request.")
        return "\n".join(parts)

    def from_ppm(self, schedule: Dict[str, Any]) -> str:
        asset_name = schedule.get("asset_name") or "Unknown asset"
        asset_id = schedule.get("asset_id") or ""
        desc = schedule.get("description") or "Scheduled maintenance"
        mtype = schedule.get("maintenance_type") or "preventive_maintenance"
        due = schedule.get("next_due_date") or "today"
        freq = schedule.get("frequency") or ""

        parts = [
            "[PPM Schedule Trigger] A planned preventive maintenance task is due.",
            f"Asset: {asset_name}" + (f" (ID: {asset_id})" if asset_id else ""),
            f"Task: {desc}",
            f"Maintenance type: {mtype}",
            f"Due date: {due}",
        ]
        if freq:
            parts.append(f"Frequency: {freq}")
        parts.append(
            "\nPlease look up this asset, run the required assessments, and create the work order."
        )
        return "\n".join(parts)

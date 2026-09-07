"""Journey log helper — appends events and detects deviations."""
from typing import Dict, Any
from datetime import datetime


class JourneyTracker:
    def append_event(
        self, journey: Dict[str, Any], step: str, details: Dict[str, Any]
    ) -> Dict[str, Any]:
        event = {"step": step, "timestamp": datetime.utcnow().isoformat(), **details}
        journey.setdefault("events", []).append(event)
        journey["current_step"] = step
        return journey

    def record_deviation(
        self, journey: Dict[str, Any], deviation_type: str, description: str
    ) -> Dict[str, Any]:
        deviation = {
            "type": deviation_type,
            "description": description,
            "detected_at": datetime.utcnow().isoformat(),
        }
        journey.setdefault("deviations", []).append(deviation)
        return journey

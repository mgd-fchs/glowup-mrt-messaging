from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class Participant:
    id: str
    platform: str
    timezone: str = "Europe/Zurich"
    active_mealtimes: List[str] = field(default_factory=list)
    custom_fields: Dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_api(raw: dict, platform: str):
        return Participant(
            id=raw.get("participantIdentifier"),
            platform=platform,
            timezone=raw.get("customFields", {}).get("timezone", "Europe/Zurich"),
            custom_fields=raw.get("customFields", {})
        )

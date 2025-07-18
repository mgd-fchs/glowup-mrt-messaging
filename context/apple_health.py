from datetime import datetime, timedelta, timezone
from pytz import timezone as pytz_timezone
from collections import defaultdict
import requests

from .base_context_provider import ContextProvider

class Provider(ContextProvider):
    def __init__(self):
        super().__init__()

    def setup(self, config: dict):
        self.namespace = "AppleHealth"
        self.config = config
        self.base_url = config.get("base_url")
        self.project_id = config.get("project_id")

    def get_steps(self, access_token, participant_identifier):
        url = f"{self.base_url}/api/v1/administration/projects/{self.project_id}/devicedatapoints"
        observed_after = (datetime.utcnow() - timedelta(hours=24)).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

        params = {
            "namespace": self.namespace,
            "type": "Steps",
            "participantIdentifier": participant_identifier,
            "observedAfter": observed_after
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json().get("deviceDataPoints", [])

    def aggregate_steps(self, data_points, participant_tz="Europe/Zurich"):
        step_totals = defaultdict(int)
        tz = pytz_timezone(participant_tz)
        
        # Current date in participant's timezone
        now_local = datetime.now(tz)
        today_local = now_local.date()

        for dp in data_points:
            if dp.get("type") != "Steps":
                continue
            try:
                # Parse and localize timestamp
                start_dt = datetime.fromisoformat(dp["startDate"].replace("Z", "+00:00"))
                start_local = start_dt.astimezone(tz)
            except Exception as e:
                print(f"Skipping invalid timestamp: {dp.get('startDate')} – {e}")
                continue

            # Check if the timestamp is from the participant's "today"
            if start_local.date() != today_local:
                print("Not today:", dp)
                continue

            try:
                source_name = dp["source"]["properties"].get("SourceName", "Unknown Source")
                step_value = int(float(dp["value"]))
                step_totals[source_name] += step_value
            except Exception as e:
                print(f"Error parsing entry: {e}")

        print(f"[{participant_tz}] Aggregation res: {dict(step_totals)}")
        return dict(step_totals)

    def get_sleep(self, access_token, participant_identifier):
        url = f"{self.base_url}/api/v1/administration/projects/{self.project_id}/devicedatapoints"
        observed_after = (datetime.utcnow() - timedelta(hours=24)).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

        params = {
            "namespace": self.namespace,
            "participantIdentifier": participant_identifier,
            "observedAfter": observed_after
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json().get("deviceDataPoints", [])

        return [dp for dp in data if dp.get("type") == "Sleep Analysis"]

    def aggregate_sleep(self, data_points, participant_tz="Europe/Zurich"):
        tz = pytz_timezone(participant_tz)
        today_local = datetime.now(tz).date()
        total_sleep_ms = 0

        for dp in data_points:
            try:
                if dp.get("type") != "Sleep Analysis":
                    continue

                start = datetime.fromisoformat(dp["startDate"].replace("Z", "+00:00")).astimezone(tz)
                end = datetime.fromisoformat(dp["endDate"].replace("Z", "+00:00")).astimezone(tz)

                if start.date() != today_local:
                    continue

                if dp.get("value") in ["Asleep", "InBed"]:
                    duration_ms = (end - start).total_seconds() * 1000
                    total_sleep_ms += duration_ms
            except Exception as e:
                print(f"[WARN] Error parsing sleep point: {e}")

        return total_sleep_ms / (1000 * 60 * 60)  # hours

from datetime import datetime, time, timezone
import requests
from collections import defaultdict
from api_utils import *


def get_steps(service_access_token, project_id, participant_identifier, base_url):
    url = f"{base_url}/api/v2/administration/projects/{project_id}/devicedatapoints"

    today = datetime.combine(datetime.utcnow().date(), time.min).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

    params = {
        "namespace": "HealthConnect",
        "type": "Steps",
        "participantIdentifier": participant_identifier,
        # "observedAfter": today
    }

    headers = {
        "Authorization": f"Bearer {service_access_token}",
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()

    return response.json().get("deviceDataPoints", [])


def aggregate_steps_by_source(data_points):
    step_totals = defaultdict(int)
    today = datetime.now(timezone.utc).date()

    for dp in data_points:
        if dp.get("type", "").lower() != "steps":
            continue

        try:
            start_date = safe_parse_iso(dp["startDate"])
            if not start_date:
                continue
        except Exception as e:
            print(f"Skipping invalid timestamp: {dp['startDate']} – {e}")
            continue

        if start_date.date() != today:
            continue

        try:
            source_name = dp["source"]["properties"].get("SourceName", "Unknown Source")
            step_value = int(dp["value"])
            step_totals[source_name] += step_value
        except Exception as e:
            print(f"Error parsing entry: {e}")

    return dict(step_totals)


def get_sleep(service_access_token, project_id, participant_identifier, base_url):
    url = f"{base_url}/api/v1/administration/projects/{project_id}/devicedatapoints"

    today = datetime.combine(datetime.utcnow().date(), time.min).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

    params = {
        "namespace": "HealthConnect",
        "participantIdentifier": participant_identifier,
        # "observedAfter": today
        # Let type be open; filter manually
    }

    headers = {
        "Authorization": f"Bearer {service_access_token}",
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()

    data = response.json().get("deviceDataPoints", [])

    # Filter entries containing sleep-related data
    return [dp for dp in data if "sleep" in dp.get("type", "").lower()]

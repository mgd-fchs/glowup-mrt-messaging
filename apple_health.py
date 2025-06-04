from datetime import datetime, timedelta, time, timezone
import pytz  # pip install pytz if not already
import requests
from collections import defaultdict


def get_steps(service_access_token, project_id, participant_identifier, base_url):
    url = f"{base_url}/api/v1/administration/projects/{project_id}/devicedatapoints"
    # TODO: Benefits of using V2?

    today = datetime.combine(datetime.utcnow().date(), time.min).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

    params = {
        "namespace": "AppleHealth",
        "type": "Steps",
        "participantIdentifier": participant_identifier,
        "observedAfter": today
    }

    headers = {
        "Authorization": f"Bearer {service_access_token}",
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, params=params)
    # print("Status:", response.status_code)
    # print("Response:", response.text)
    response.raise_for_status()

    return response.json().get("deviceDataPoints", [])


def aggregate_steps_by_source(data_points):
    step_totals = defaultdict(int)
    today = datetime.now(timezone.utc).date()

    for dp in data_points:
        if dp.get("type") != "Steps":
            continue

        # Parse the start date and convert to UTC-aware datetime
        try:
            start_date = datetime.fromisoformat(dp["startDate"].replace("Z", "+00:00"))
        except Exception as e:
            print(f"Skipping invalid timestamp: {dp['startDate']} – {e}")
            continue

        if start_date.date() != today:
            continue  # skip data points not from today

        try:
            source_name = dp["source"]["properties"].get("SourceName", "Unknown Source")
            step_value = int(float(dp["value"]))
            step_totals[source_name] += step_value
        except Exception as e:
            print(f"Error parsing entry: {e}")

    return dict(step_totals)


def get_sleep(service_access_token, project_id, participant_identifier, base_url):
    url = f"{base_url}/api/v1/administration/projects/{project_id}/devicedatapoints"

    today = datetime.combine(datetime.utcnow().date(), time.min).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

    params = {
        "namespace": "AppleHealth",
        "participantIdentifier": participant_identifier,
        "observedAfter": today
        # No "type": "Sleep Analysis"
    }

    headers = {
        "Authorization": f"Bearer {service_access_token}",
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()

    data = response.json().get("deviceDataPoints", [])

    # Filter only Sleep Analysis entries
    return [dp for dp in data if dp.get("type") == "Sleep Analysis"]
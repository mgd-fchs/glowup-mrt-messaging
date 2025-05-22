from datetime import datetime, timedelta, time, timezone
import pytz  # pip install pytz if not already
import requests
from collections import defaultdict

def is_currently_in_mealtime_window(start_time_str: str, local_time: datetime.time) -> bool:
    
    # TODO: Handle edge-cases: What happens if a meal window starts at 23:00 or 00:00?? --> input validation
    try:
        # Parse '01:00 PM' correctly
        start_time = datetime.strptime(start_time_str.strip(), "%I:%M %p").time()
        today = datetime.today()
        start_dt = datetime.combine(today, start_time)
        end_dt = start_dt + timedelta(hours=2)
        local_dt = datetime.combine(today, local_time)
        return start_dt <= local_dt <= end_dt
    
    except Exception as e:
        print(f"Failed to parse '{start_time_str}': {e}")
        return False


def get_active_meal_window_participants(participants):
    active_participants = []

    for p in participants:
        custom_fields = p.get("customFields", {})
        participant_tz_name = p.get("demographics", {}).get("timeZone", "UTC")
        # print(participant_tz_name)
        try:
            participant_tz = pytz.timezone(participant_tz_name)
        except Exception as e:
            print(f"Invalid timezone '{participant_tz_name}' for participant {p['id']}, defaulting to UTC: {e}")
            participant_tz = pytz.UTC
            # TODO: Test time zone differences

        now_local = datetime.now(participant_tz).time()

        # Check if any 'mealtime_' field is active
        # print(custom_fields.items())
        # print(now_local)
        is_active = any(
            is_currently_in_mealtime_window(value, now_local)
            for key, value in custom_fields.items()
            if key.startswith("mealtime_") and value
        )

        if is_active:
            active_participants.append(p)

    return active_participants


def get_apple_health_steps(service_access_token, project_id, participant_identifier, base_url):
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
            step_value = int(dp["value"])
            step_totals[source_name] += step_value
        except Exception as e:
            print(f"Error parsing entry: {e}")

    return dict(step_totals)


def get_apple_health_sleep(service_access_token, project_id, participant_identifier, base_url):
    url = f"{base_url}/api/v2/administration/projects/{project_id}/devicedatapoints/aggregate/dailysleep"

    today = datetime.combine(datetime.utcnow().date(), time.min).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

    params = {
        "namespace": "AppleHealth",
        "type": "Sleep Analysis",
        "participantIdentifier": participant_identifier,
        # "observedAfter": today
    }

    headers = {
        "Authorization": f"Bearer {service_access_token}",
        "Accept": "application/json"
    }

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()

    # Returns a list like your steps function
    return response.json().get("sleepStageSummaries", [])
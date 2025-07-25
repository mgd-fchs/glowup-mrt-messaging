from datetime import datetime, timedelta
import pytz
import api_utils

def get_participants_by_segment(project_id, access_token, segment_id):
    participants = []
    page = 0
    while True:
        url = f'api/v1/administration/projects/{project_id}/participants?segmentId={segment_id}&pageNumber={page}&pageSize=100'
        response = api_utils.get_from_api(access_token, url)
        data = response.json()
        participants.extend(data.get("participants", []))
        if len(data.get("participants", [])) < 100:
            break
        page += 1
    return participants

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
        try:
            participant_tz = pytz.timezone(participant_tz_name)
        except Exception as e:
            print(f"Invalid timezone '{participant_tz_name}' for participant {p['id']}, defaulting to UTC: {e}")
            participant_tz = pytz.UTC

        now_local_dt = datetime.now(participant_tz)
        now_local_time = now_local_dt.time()
        weekday = now_local_dt.weekday()  # Monday = 0, Sunday = 6

        # Choose prefix based on weekday
        if weekday < 5:
            prefix = "mealtime_mon_"
        else:
            prefix = "mealtime_we_"

        active_mealtimes = [
            key for key, value in custom_fields.items()
            if key.startswith(prefix) and value and is_currently_in_mealtime_window(value, now_local_time)
        ]

        if active_mealtimes:
            p["active_mealtimes"] = active_mealtimes
            active_participants.append(p)

    return active_participants




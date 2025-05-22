from datetime import datetime, timedelta
import pytz

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


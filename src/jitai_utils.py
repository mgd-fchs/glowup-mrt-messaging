from datetime import datetime, time, timedelta
import pytz  # pip install pytz if not already

def is_currently_in_mealtime_window(start_time_str: str, current_time: datetime.time) -> bool:
    try:
        # Parse '01:00 PM' correctly
        start_time = datetime.strptime(start_time_str.strip(), "%I:%M %p").time()

        today = datetime.today()
        start_dt = datetime.combine(today, start_time)
        end_dt = start_dt + timedelta(hours=2)

        return start_dt.time() <= current_time <= end_dt.time()
    except Exception as e:
        print(f"Failed to parse '{start_time_str}': {e}")
        return False


def get_active_meal_window_participants(participants):
    from_zone = pytz.timezone("Europe/Zurich")
    now = datetime.now(from_zone).time()

    active_participants = []

    for p in participants:
        custom_fields = p.get("customFields", {})
        print(custom_fields)
        # Check if any 'mealtime_' field is active
        is_active = any(
            is_currently_in_mealtime_window(value, now)
            for key, value in custom_fields.items()
            if key.startswith("mealtime_") and value
        )
        if is_active:
            active_participants.append(p["id"])

    return active_participants

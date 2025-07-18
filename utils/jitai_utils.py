from datetime import datetime, timedelta
import pytz
import importlib
import os
import sys

import utils.api_utils as api_utils
import traceback

def load_context_providers(context_config, shared_config=None):
    providers = {}
    for platform, module_path in context_config.items():
        try:
            module = importlib.import_module(module_path)
            provider_class = getattr(module, "Provider")
            instance = provider_class()
            if hasattr(instance, "setup"):
                instance.setup(shared_config)
            providers[platform] = instance
        except Exception as e:
            print(f"[ERROR] Failed to load provider for {platform}: {e}")
            traceback.print_exc()
    return providers

def evaluate_sync_reminder(participant_signals, logic_config):
    for rule in logic_config.get("conditions", []):
        signal = rule.get("signal")
        condition = rule.get("condition", None)
        value = participant_signals.get(signal)

        if condition is None:
            if value is None:
                return True
        else:
            try:
                if eval(f"value {condition}"):
                    return True
            except Exception as e:
                print(f"[WARN] Failed to evaluate condition '{condition}' for {signal}: {e}")
    return False

def is_available_for_decision(now_utc, participant_obj, point_config):
    tz_name = get_nested_value(participant_obj, point_config.get("timezone_field", "customFields.timezone")) or "Europe/Zurich"
    now_local = now_utc.astimezone(pytz.timezone(tz_name))
    strategy = point_config["strategy"]

    if strategy == "fixed_time":
        target = datetime.strptime(point_config["time"], "%H:%M").time()
        return now_local.time().hour == target.hour and now_local.time().minute == target.minute

    elif strategy == "random_window":
        start = datetime.strptime(point_config["window"]["start"], "%H:%M").time()
        end = datetime.strptime(point_config["window"]["end"], "%H:%M").time()
        return start <= now_local.time() <= end

    elif strategy == "user_defined":
        times = get_nested_value(participant_obj, point_config["context_key"])
        return now_local.strftime("%H:%M") in (times or [])
    
    elif strategy == "random_relative_window":
        base_time_str = get_nested_value(participant_obj, point_config["base_time_field"])
        if not base_time_str:
            return False
        try:
            base_time = datetime.strptime(base_time_str, "%H:%M").time()
        except ValueError:
            return False

    return False


def get_nested_value(obj, path):
    keys = path.split(".")
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key, {})
        else:
            return None
    return obj if obj else None

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

        now_local = datetime.now(participant_tz).time()

        active_mealtimes = [
            key for key, value in custom_fields.items()
            if key.startswith("mealtime_") and value and is_currently_in_mealtime_window(value, now_local)
        ]

        if active_mealtimes:
            p["active_mealtimes"] = active_mealtimes  # Add to participant dict
            active_participants.append(p)

    return active_participants



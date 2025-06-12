import requests
import random
from datetime import datetime, timedelta, timezone
import boto3
import json
from botocore.exceptions import ClientError
from dateutil import parser
from s3_utils import *
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone
import random
from dateutil import parser
from pytz import timezone as pytz_timezone

BUCKET = "mrt-messages-logs"
SENT_LOG_KEY = "sent_log.json"


# Mock notification bank
NOTIFICATION_BANK = {
    "control": ["neutral_00", "neutral_01", "neutral_02", "neutral_03", "neutral_04"],
    "context_pos": ["mood_pos_00"],
    "context_neg": ["mood_neg_00"],
    "context_missing": ["mood_null_00", "mood_null_01"],
    "loss_fin": ["loss_fin_00", "loss_fin_01", "loss_fin_02", "loss_fin_03"],
    "loss_streak": ['loss_streak_00', 'loss_streak_01', 'loss_streak_02', 'loss_streak_03']
}

def get_random_send_time(start_str, tz_str="Europe/Zurich"):
    parsed_time = parser.parse(start_str).time()
    tz = pytz_timezone(tz_str)
    today = datetime.now(tz).date()
    start_dt = tz.localize(datetime.combine(today, parsed_time))
    end_dt = start_dt + timedelta(hours=2)

    now_local = datetime.now(tz)
    if now_local > end_dt:
        raise ValueError("Time window already passed.")

    lower_bound = max(start_dt, now_local)
    delta_minutes = int((end_dt - lower_bound).total_seconds() // 60)
    random_offset = random.randint(0, delta_minutes)
    send_time_local = lower_bound + timedelta(minutes=random_offset)

    return send_time_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def log_notification(bucket, participant_record):
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_key = f"logs/{date_str}.json"
    s3 = boto3.client('s3')

    line = json.dumps(participant_record) + "\n"
    try:
        # Append by reading and rewriting the whole file (simple fallback)
        obj = s3.get_object(Bucket=bucket, Key=log_key)
        existing = obj['Body'].read().decode('utf-8')
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            existing = ""
        else:
            raise
    updated = existing + line
    s3.put_object(Body=updated.encode('utf-8'), Bucket=bucket, Key=log_key)


def log_notification_to_s3(record):
    BUCKET = "mrt-messages-logs"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_key = f"logs/{date_str}.jsonl"
    s3 = boto3.client('s3')
    line = json.dumps(record) + "\n"

    try:
        obj = s3.get_object(Bucket=BUCKET, Key=log_key)
        existing = obj['Body'].read().decode('utf-8')
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            existing = ""
        else:
            raise

    updated = existing + line
    s3.put_object(Body=updated.encode('utf-8'), Bucket=BUCKET, Key=log_key)


def send_notifications(service_access_token, project_id, participant_context_data):
    url = f"https://mydatahelps.org/api/v1/administration/projects/{project_id}/notifications"
    headers = {
        "Authorization": f"Bearer {service_access_token}",
        "Content-Type": "application/json"
    }

    scheduled_log = load_log(BUCKET, "scheduled_log.json", dated=True)
    sent_log = load_log(BUCKET, "sent_log.json", dated=True)
    now_utc = datetime.now(timezone.utc)

    for key, scheduled_time_str in scheduled_log.items():
        pid, mealtime = key.split("::")
        try:
            scheduled_time = datetime.fromisoformat(scheduled_time_str.replace("Z", "+00:00"))
        except Exception as e:
            print(f"Invalid time format for {key}: {scheduled_time_str} – {e}")
            continue

        if scheduled_time > now_utc:
            continue  # not yet time

        if key in sent_log:
            continue  # already sent

        context = participant_context_data.get(pid, {})
        group = context.get("group")  # Must be injected during randomization/scheduling
        if not group:
            print(f"No group assignment found for {pid} – skipping")
            continue

        notification_options = NOTIFICATION_BANK.get(group, [])
        if not notification_options:
            print(f"No messages available for group '{group}' – skipping {key}")
            continue

        notification_id = random.choice(notification_options)

        payload = [{
            "participantIdentifier": pid,
            "notificationIdentifier": notification_id,
            "sendTime": now_utc.isoformat() + "Z"
        }]

        response = requests.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            print(f"Sent '{notification_id}' to '{pid}' for {mealtime}")
            sent_log[key] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

            log_record = {
                "participant_id": pid,
                "group": group,
                "mealtime": mealtime,
                "notification_id": notification_id,
                "send_time": now_utc.isoformat() + "Z",
                "total_steps": context.get("total_steps"),
                "total_sleep_hours": context.get("total_sleep_hours")
            }
            log_notification_to_s3(log_record)
        else:
            print(f"Failed to send to '{pid}' ({group}): {response.status_code} - {response.text}")

    save_log(BUCKET, "sent_log.json", sent_log)


def schedule_notifications(assignments, participant_context_data):
    scheduled_log = load_log(BUCKET, "scheduled_log.json", dated=True)

    for pid, group in assignments.items():
        participant_context_data[pid]["group"] = group  # inject group info for later use

        mealtimes = participant_context_data.get(pid, {}).get("active_mealtimes", [])
        if not mealtimes:
            print(f"{pid} has no active mealtime(s) – skipping scheduling.")
            continue

        custom_fields = participant_context_data[pid].get("custom_fields", {})

        for mealtime in mealtimes:
            key = f"{pid}::{mealtime}"
            if key in scheduled_log:
                print(f"{key} already scheduled.")
                continue

            mealtime_value = custom_fields.get(mealtime)  # e.g., "12:00"
            if not mealtime_value:
                print(f"{key} missing mealtime value in custom fields — skipping.")
                continue

            try:
                send_time = get_random_send_time(mealtime_value)
            except Exception as e:
                print(f"{key} invalid time format '{mealtime_value}' — {e}")
                continue

            notification_options = NOTIFICATION_BANK.get(group, [])
            if not notification_options:
                print(f"No notification for group '{group}' — skipping {key}")
                continue

            notification_id = random.choice(notification_options)

            scheduled_log[key] = {
                "participant_id": pid,
                "mealtime": mealtime,
                "group": group,
                "notification_id": notification_id,
                "send_time": send_time
            }

            print(f"Scheduled: {key} at {send_time} with '{notification_id}' ({group})")

    save_log(BUCKET, "scheduled_log.json", scheduled_log)


def randomize(participant_context_data):
    """
    Assigns participants to groups based on context (steps/sleep).
    If:
    - Both steps and sleep are missing (0): 'context_missing'
    - Only one is available: use that one for classification
    - Both available:
        - steps high + sleep high → 'context_pos'
        - steps low + sleep high → 'context_pos'
        - steps high + sleep low → 'context_neg'
        - steps low + sleep low → 'context_neg'

    Returns:
        dict mapping participant ID to group
    """
    assignments = {}

    for pid, context in participant_context_data.items():
        steps = context.get("total_steps")
        sleep = context.get("total_sleep_hours")

        # Missing data handling
        steps_available = steps is not None and steps > 0
        sleep_available = sleep is not None and sleep > 0

        if not steps_available and not sleep_available:
            group = "context_missing"
        else:
            steps_high = steps_available and (
                (context.get("time_of_day") == "lunch" and steps >= 2500) or
                (context.get("time_of_day") == "dinner" and steps >= 5000)
            )

            sleep_high = sleep_available and 6.5 <= sleep <= 9

            if steps_available and not sleep_available:
                group = "context_pos" if steps_high else "context_neg"
            elif sleep_available and not steps_available:
                group = "context_pos" if sleep_high else "context_neg"
            else:
                if steps_high and sleep_high:
                    group = "context_pos"
                elif steps_high and not sleep_high:
                    group = "context_neg"
                elif not steps_high and sleep_high:
                    group = "context_pos"
                else:
                    group = "context_neg"

        assignments[pid] = group

    return assignments

import requests

def set_custom_field(access_token, project_id, participant_id, field_name, value):
    url = f"https://mydatahelps.org/api/v1/administration/projects/{project_id}/participants/{participant_id}/customfields"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        field_name: value
    }

    response = requests.put(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to update field '{field_name}' for participant '{participant_id}': "
            f"{response.status_code} - {response.text}"
        )

import requests
import random
from datetime import datetime, timedelta, timezone
import boto3
import json
from botocore.exceptions import ClientError
from dateutil import parser
from s3_utils import *


BUCKET = "mrt-messages-logs"
SENT_LOG_KEY = "sent_log.json"


# Mock notification bank
NOTIFICATION_BANK = {
    "control": ["neutral_00", "neutral_01"],
    "context": ["mood_neg_00", "mood_neg_01"],
    "loss": ["loss_fin_00", "loss_fin_00"]
}


def get_random_send_time(start_str, tzinfo=timezone.utc):
    """
    Given a time string like '12:00' or '02:30 PM', returns a random datetime
    today between that time and +2h, in UTC.
    """
    try:
        parsed_time = parser.parse(start_str).time()  # supports AM/PM and 24h formats
    except Exception as e:
        raise ValueError(f"Unparseable time string '{start_str}': {e}")

    today = datetime.now(tzinfo).date()
    start_dt = datetime.combine(today, parsed_time, tzinfo)
    end_dt = start_dt + timedelta(hours=2)

    delta_minutes = int((end_dt - start_dt).total_seconds() // 60)
    random_offset = random.randint(0, delta_minutes)
    send_time = start_dt + timedelta(minutes=random_offset)

    return send_time.isoformat().replace("+00:00", "Z")


def log_notification(bucket, participant_record):
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_key = f"logs/{date_str}.jsonl"
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
    Randomizes participants into 'control', 'context', or 'loss' arms.
    If a participant has missing step or sleep data, only randomize between 'control' and 'loss'.

    Parameters:
    - participant_context_data: dict keyed by participant ID with 'total_steps' and 'total_sleep_hours'

    Returns:
    - dict mapping participant ID to assigned group
    """
    assignments = {}

    for pid, context in participant_context_data.items():
        has_steps = context.get("total_steps") is not None
        # has_sleep = context.get("total_sleep_hours") is not None
        has_sleep=True

        if has_steps and has_sleep:
            group = random.choice(["control", "context", "loss"])
        else:
            group = random.choice(["control", "loss"])

        assignments[pid] = group

    return assignments
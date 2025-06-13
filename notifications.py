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
    "control": [
        "control_00", "control_01", "control_02", "control_03", "control_04"
    ],
    "dual_high": [
        "context_high_00", "context_high_01", "context_high_02", "context_high_03", "context_high_04"
    ],
    "dual_low": [
        "context_low_00", "context_low_01", "context_low_02", "context_low_03", "context_low_04"
    ],
    "single": [
        "loss_00", "loss_01", "loss_02", "loss_03", "loss_04"
    ]
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
    print(f"Participant data: {participant_context_data}")
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

            # Safely get values, defaulting to 0 if missing or None
            tracking_count = participant_context_data[pid]['custom_fields'].get("TrackingCount")
            print(f"Tracking count: {tracking_count}")
            if not tracking_count:
                tracking_count = 0
            else: tracking_count = int(tracking_count)

            surveys_delivered = participant_context_data[pid]['custom_fields'].get("SurveysDelivered")
            print(f"Surveys delivered: {surveys_delivered}")
            if surveys_delivered == 0:
                surveys_delivered = 1
            else: surveys_delivered = int(surveys_delivered)
            # Calculate ratio with +1 in denominator to avoid division by zero
            tracking_ratio = (tracking_count / (surveys_delivered)) * 100

            # Dynamic group reassignment based on tracking ratioß
            print(f"Ratio:{tracking_ratio}")
            if group == "context":
                if tracking_ratio >= 75:
                    group = "dual_high"
                else:
                    group = "dual_low"

            # Proceed with notification selection
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
        group = random.choice(["context", "control", "single"])
        assignments[pid] = group

    return assignments
import os
import requests
import random
from datetime import datetime, timedelta, timezone
import boto3
import json
from botocore.exceptions import ClientError
from dateutil import parser
from s3_utils import *
from zoneinfo import ZoneInfo
from pytz import timezone as pytz_timezone

BUCKET = "glowup-mrt"
SENT_LOG_KEY = "sent_log.json"

NOTIFICATION_BANK = {
    "control": ["control_00", "control_01", "control_02", "control_03", "control_04", "control_05", "control_06", "control_07", "control_08", "control_09", "control_10", "control_11", "control_12", "control_13", "control_14"],
    "dual_high": ["context_high_00", "context_high_01", "context_high_02", "context_high_03", "context_high_04", "context_high_05", "context_high_06", "context_high_07", "context_high_08", "context_high_09", "context_high_10", "context_high_11", "context_high_12", "context_high_13", "context_high_14"],
    "dual_low": ["context_low_00", "context_low_01", "context_low_02", "context_low_03", "context_low_04", "context_low_05", "context_low_06", "context_low_07", "context_low_08", "context_low_09", "context_low_10", "context_low_11", "context_low_12", "context_low_13", "context_low_14"],
    "single": ["loss_00", "loss_01", "loss_02", "loss_03", "loss_04", "loss_05", "loss_06", "loss_07", "loss_08", "loss_09", "loss_10", "loss_11", "loss_12", "loss_13", "loss_14"]
}

# ### IF ENGLISH VERSION
# NOTIFICATION_BANK = {
#     key: [f"{nid}_en" for nid in ids]
#     for key, ids in NOTIFICATION_BANK.items()
# }

def get_random_send_time(start_str, tz_str="Europe/Zurich"):
    parsed_time = parser.parse(start_str).time()
    tz = pytz_timezone(tz_str)
    today = datetime.now(tz).date()
    start_dt = tz.localize(datetime.combine(today, parsed_time))
    end_dt = start_dt + timedelta(minutes=30)

    now_local = datetime.now(tz)
    if now_local > end_dt:
        raise ValueError("Time window already passed.")

    lower_bound = max(start_dt, now_local)
    delta_minutes = int((end_dt - lower_bound).total_seconds() // 60)
    random_offset = random.randint(0, delta_minutes)
    send_time_local = lower_bound + timedelta(minutes=random_offset)

    return send_time_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def log_notification_to_s3(record):
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_key = f"logs/{date_str}.json"
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


def load_tracking_log(bucket, log_key):
    s3 = boto3.client('s3')
    try:
        obj = s3.get_object(Bucket=bucket, Key=log_key)
        log = obj['Body'].read().decode('utf-8').splitlines()
        return [json.loads(line) for line in log]
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            return []
        else:
            raise


def log_tracking_update(bucket, log_key, entry):
    s3 = boto3.client('s3')
    try:
        obj = s3.get_object(Bucket=bucket, Key=log_key)
        existing = obj['Body'].read().decode('utf-8')
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            existing = ""
        else:
            raise
    updated = existing + json.dumps(entry) + "\n"
    s3.put_object(Body=updated.encode('utf-8'), Bucket=bucket, Key=log_key)


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
        pid, mealtime = key.split("::")[0], key.split("::")[1]
        try:
            scheduled_time = datetime.fromisoformat(scheduled_time_str["send_time"].replace("Z", "+00:00")) if isinstance(scheduled_time_str, dict) else datetime.fromisoformat(scheduled_time_str.replace("Z", "+00:00"))
        except Exception as e:
            print(f"Invalid time format for {key}: {scheduled_time_str} – {e}")
            continue

        if scheduled_time > now_utc:
            continue

        if key in sent_log:
            continue

        context = participant_context_data.get(pid, {})
        group = context.get("group") or scheduled_log[key].get("group")
        if not group:
            print(f"No group assignment found for {pid} – skipping")
            continue

        notification_options = NOTIFICATION_BANK.get(group, [])
        if not notification_options:
            if group == "sync_reminder":
                notification_options = ["sync_reminder"]
                # IF ENGLISH
                # notification_options = ["sync_reminder_en"]
            else:
                print(f"No messages available for group '{group}' – skipping {key}")
                continue

        notification_id = scheduled_log[key].get("notification_id") or random.choice(notification_options)

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
            save_log(BUCKET, "sent_log.json", sent_log)
        else:
            print(f"Failed to send to '{pid}' ({group}): {response.status_code} - {response.text}")


def schedule_sync_reminders(participant_context_data):
    scheduled_log = load_log(BUCKET, "scheduled_log.json", dated=True)
    now_utc = datetime.now(timezone.utc)

    for pid, context in participant_context_data.items():
        if not context.get("needs_sync_reminder"):
            continue

        key = f"{pid}::sync"
        existing_entry = scheduled_log.get(key)

        if existing_entry:
            try:
                # Ensure we always treat scheduled_log[key] as a dict
                if not isinstance(existing_entry, dict) or "send_time" not in existing_entry:
                    print(f"Malformed existing entry for {key}, skipping scheduling.")
                    continue

                last_time = datetime.fromisoformat(existing_entry["send_time"].replace("Z", "+00:00"))

                if last_time > now_utc:
                    print(f"{key} already scheduled in the future at {last_time} — skipping")
                    continue
                elif (now_utc - last_time) < timedelta(hours=4):
                    print(f"{key} was sent less than 4h ago at {last_time} — skipping")
                    continue
            except Exception as e:
                print(f"Error parsing send_time for {key}: {e}")
                continue

        send_time = (now_utc + timedelta(minutes=random.randint(0, 10))).isoformat().replace("+00:00", "Z")

        scheduled_log[key] = {
            "participant_id": pid,
            "mealtime": "NA",
            "group": "sync_reminder",
            "notification_id": "sync_reminder",
            "send_time": send_time
        }

        # # IF ENGLISH
        # scheduled_log[key] = {
        #     "participant_id": pid,
        #     "mealtime": "NA",
        #     "group": "sync_reminder",
        #     "notification_id": "sync_reminder_en",
        #     "send_time": send_time
        # }
        print(f"Scheduled sync_reminder for {pid} at {send_time}")

    save_log(BUCKET, "scheduled_log.json", scheduled_log)



def schedule_notifications(assignments, participant_context_data):
    scheduled_log = load_log(BUCKET, "scheduled_log.json", dated=True)
    for pid, group in assignments.items():
        print(participant_context_data[pid])
        tz_str = participant_context_data[pid].get("demographics", {}).get("timeZone")
        print(f"[DEBUG] Timezone for scheduling for participant {pid}: {tz_str}")
        
        participant_context_data[pid]["group"] = group
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
            mealtime_value = custom_fields.get(mealtime)
            if not mealtime_value:
                print(f"{key} missing mealtime value in custom fields — skipping.")
                continue
            try:
                send_time = get_random_send_time(mealtime_value, tz_str=tz_str)
                print(f"[DEBUG] Scheduled time for participant {participant_context_data[pid]}: {send_time}")
            except Exception as e:
                print(f"{key} invalid time format '{mealtime_value}' — {e}")
                continue
            tracking_count = participant_context_data[pid]['custom_fields'].get("TrackingCount")
            if not tracking_count:
                tracking_count = 0
            else:
                tracking_count = int(tracking_count)
            surveys_delivered = participant_context_data[pid]['custom_fields'].get("SurveysDelivered")
            if surveys_delivered == 0:
                surveys_delivered = 1
            else:
                surveys_delivered = int(surveys_delivered)
            tracking_ratio = (tracking_count / surveys_delivered) * 100
            if group == "context":
                if tracking_ratio >= 75:
                    group = "dual_high"
                else:
                    group = "dual_low"
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

    for pid in participant_context_data.keys():
        group = random.choice(["context", "control", "single"])
        assignments[pid] = group

    return assignments


def check_and_increment_tracking(base_url, project_id, access_token, bucket):
    url = f"{base_url}/api/v1/administration/projects/{project_id}/surveytasks"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    params = {
        "pageSize": 200  # Adjust as needed
    }

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"Failed to fetch survey tasks: {response.status_code}, {response.text}")
        return

    today = datetime.now(timezone.utc).date()

    print(f"[DEBUG] For task completion, today is {today}")

    completed_tasks = [
        t for t in response.json().get("surveyTasks", [])
        if (
            t.get("status", "").lower() == "complete" and
            t.get("surveyName") in {"log_breakfast_de", "log_lunch_de", "log_dinner_de"} and
            t.get("endDate") and
            parser.parse(t["endDate"]).date() == today
        )
    ]

    # # IF ENGLISH
    # completed_tasks = [
    #     t for t in response.json().get("surveyTasks", [])
    #     if (
    #         t.get("status", "").lower() == "complete" and
    #         t.get("surveyName") in {"log_breakfast_en", "log_lunch_en", "log_dinner_en"} and
    #         t.get("endDate") and
    #         parser.parse(t["endDate"]).date() == today
    #     )
    # ]

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_key = f"logs/tracking_{today_str}.json"
    log_entries = load_tracking_log(bucket, log_key)
    already_logged = set(
        (entry["participantIdentifier"], entry["surveyName"], entry["completedDate"])
        for entry in log_entries
        if "completedDate" in entry
    )
    # print(f"Completed tasks: {completed_tasks}")
    # print(f"Already logged: {already_logged}")

    for task in completed_tasks:
        pid = task.get("participantIdentifier")
        survey_name = task.get("surveyName")
        completed_time = task.get("endDate") or task.get("endDate")

        if not pid or not survey_name or not completed_time:
            continue

        completion_day = completed_time[:10] if isinstance(completed_time, str) else completed_time.date().isoformat()

        if (pid, survey_name, completion_day) in already_logged:
            continue

        # Fetch current custom field
        participant_url = f"{base_url}/api/v1/administration/projects/{project_id}/participants/{pid}"
        participant_resp = requests.get(participant_url, headers=headers)
        if participant_resp.status_code != 200:
            print(f"Failed to fetch participant {pid}: {participant_resp.status_code}")
            continue

        participant_data = participant_resp.json()
        current_val = participant_data.get("customFields", {}).get("TrackingCount", 0)
        try:
            new_val = int(current_val) + 1
        except:
            new_val = 1

        update_url = f"{base_url}/api/v1/administration/projects/{project_id}/participants"
        update_payload = {
            "participantIdentifier": pid,
            "customFields": {
                "TrackingCount": new_val
            }
        }

        update_resp = requests.put(update_url, headers=headers, json=update_payload)
        if update_resp.status_code == 200:
            print(f"Updated TrackingCount for {pid} to {new_val}")
            log_tracking_update(bucket, log_key, {
                "participantIdentifier": pid,
                "surveyName": survey_name,
                "completedDate": completion_day,
                "newTrackingCount": new_val
            })
        else:
            print(f"Failed to update TrackingCount for {pid}: {update_resp.status_code}, {update_resp.text}")


def has_incomplete_task_today(pid, mealtime, project_id, access_token):

    # Extract just the part indicating the meal type
    if mealtime and mealtime.startswith("mealtime_"):
        parts = mealtime.split("_")
        mealtime_type = parts[-1]  # works for mealtime_mon_breakfast, etc.
    else:
        mealtime_type = mealtime

    survey_map = {
        "breakfast": "log_breakfast_de",
        "lunch": "log_lunch_de",
        "dinner": "log_dinner_de"
    }

    # ## IF ENGLISH
    # survey_map = {
    #     "breakfast": "log_breakfast_en",
    #     "lunch": "log_lunch_en",
    #     "dinner": "log_dinner_en"
    # }

    if mealtime_type not in survey_map:
        print(f"[WARN] Unknown or irrelevant mealtime '{mealtime}' for participant {pid} — skipping check")
        return True  # Fallback: treat as incomplete to be safe

    survey_name = survey_map[mealtime_type]
    url = f"https://mydatahelps.org/api/v1/administration/projects/{project_id}/surveytasks"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    params = {
        "pageSize": 100,
        "participantIdentifier": pid
    }

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"[ERROR] Failed to fetch tasks for {pid}: {response.status_code}")
        return True  # Fallback to 'incomplete'

    try:
        tasks = response.json().get("surveyTasks", [])
    except Exception as e:
        print(f"[ERROR] Failed to parse surveyTasks for {pid}: {e}")
        return True

    today_utc = datetime.now(timezone.utc).date() # TODO adapt to participants' timezone
    found_task = False
    for task in tasks:
        if task.get("surveyName") != survey_name:
            continue

        inserted_str = task.get("insertedDate")
        if not inserted_str:
            continue

        try:
            inserted_dt = parser.parse(inserted_str).astimezone(timezone.utc)
        except Exception:
            continue

        if inserted_dt.date() != today_utc:
            continue

        found_task = True
        task_status = task.get("status", "").lower()
        print(f"[DEBUG] {pid} — found '{survey_name}' inserted at {inserted_dt.isoformat()} with status '{task_status}'")

        if task_status == "incomplete":
            return True  # eligible to send

    if not found_task:
        print(f"[DEBUG] No {survey_name} task found for {pid} today.")
    else:
        print(f"[DEBUG] {pid} already completed {survey_name} today.")

    return False  # default: do not send


def send_email(participant_id):
    ses = boto3.client('ses', region_name='eu-central-1')

    recipient = os.getenv('EMAIL_RECIPIENT_1')
    subject = "⚠️ No sensor data received in last 24h"

    body = f"""
    Hello,

    No new Ultrahuman sensor data has been received for participant {participant_id} in the past 24 hours.
    Please check the participant’s device or connectivity status.

    — Automated Monitoring via AWS
    """

    ses.send_email(
        Source="healthlab.css@gmail.com",  # must be a verified sender
        Destination={"ToAddresses": [recipient]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body}}
        }
    )

    print(f"[INFO] Alert email sent for participant {participant_id}")
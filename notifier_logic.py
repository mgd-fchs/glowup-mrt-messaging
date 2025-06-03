import requests
from datetime import datetime, timezone
from notifications import *
from api_utils import *
from jitai_utils import *

BUCKET = "mrt-messages-logs"

def lambda_handler(event=None, context=None):
    print("Running notifier...")

    scheduled_log = load_log(BUCKET, "scheduled_log.json", dated=True)
    sent_log = load_log(BUCKET, "sent_log.json", dated=True)
    now_utc = datetime.now(timezone.utc)

    print(f"project_id: {project_id}")
    access_token = get_service_access_token()

    url = f"https://mydatahelps.org/api/v1/administration/projects/{project_id}/notifications"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    if not scheduled_log:
        print("No notifications scheduled for today.")
        return

    total = len(scheduled_log)
    future_count = 0
    already_sent_count = 0
    sent_now_count = 0

    for key, record in scheduled_log.items():
        pid = record["participant_id"]
        mealtime = record["mealtime"]
        group = record["group"]
        notification_id = record["notification_id"]
        scheduled_time_str = record["send_time"]

        if key in sent_log:
            already_sent_count += 1
            continue

        try:
            scheduled_time = datetime.fromisoformat(scheduled_time_str.replace("Z", "+00:00"))
        except Exception as e:
            print(f"Invalid timestamp for {key}: {scheduled_time_str} – {e}")
            continue

        if scheduled_time > now_utc:
            future_count += 1
            continue

        # Time to send
        payload = [{
            "participantIdentifier": pid,
            "notificationIdentifier": notification_id,
            "sendTime": now_utc.isoformat() + "Z"
        }]

        print(f"Sending payload: {json.dumps(payload, indent=2)}")
        response = requests.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            print(f"Sent {notification_id} to {pid} ({group}) [{mealtime}]")

            log_entry = {
                "participant_id": pid,
                "group": group,
                "mealtime": mealtime,
                "notification_id": notification_id,
                "scheduled_time": scheduled_time_str,
                "actual_send_time": now_utc.isoformat() + "Z"
            }

            sent_log[key] = log_entry
            log_notification_to_s3(log_entry)
            sent_now_count += 1

        else:
            print(f"Failed to send to {pid}: {response.status_code} - {response.text}")

    save_log(BUCKET, "sent_log.json", sent_log)

    # Final summary
    print("Notifier run complete.")
    print(f"Total scheduled entries: {total}")
    print(f"Already sent: {already_sent_count}")
    print(f"Scheduled for future: {future_count}")
    print(f"Sent now: {sent_now_count}")

    if sent_now_count == 0:
        if already_sent_count == total:
            print("All notifications already sent.")
        elif future_count == total:
            print("All scheduled notifications are for the future.")
        else:
            print("No eligible notifications to send at this time.")


if __name__ == "__main__":
    lambda_handler()
import os
import boto3
import requests
from datetime import datetime, timedelta, timezone
from api_utils import *

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("UH_emails")
ses = boto3.client("ses", region_name="eu-north-1")

MDH_BASE_URL = "https://mydatahelps.org"


def send_inactive_email(participant_id, participant_email, last_ts, hours_ago):
    recipient_1 = os.environ["EMAIL_RECIPIENT_1"].strip()
    recipient_2 = os.environ["EMAIL_RECIPIENT_2"].strip()
    sender = os.environ["EMAIL_SENDER"].strip()

    subject = "UH participant inactive"
    body_text = (
        f"The participant with ID {participant_id}, email {participant_email} has not synchronized their Ultrahuman data.\n"
        f"Last timestamp: {last_ts}\n"
        f"Hours since last update: {hours_ago}\n"
        f"Please follow up with the participant."
    )

    ses.send_email(
        Source=sender,
        Destination={"ToAddresses": [recipient_1, recipient_2]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body_text}},
        },
    )


def find_mdh_participant_by_email(project_id, access_token, email):
    url = f"{MDH_BASE_URL}/api/v1/administration/projects/{project_id}/participants"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    page = 0

    while True:
        params = {"pageSize": 200, "pageNumber": page}
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        batch = data.get("participants", [])
        print(f"[DEBUG] Page {page}: scanned {len(batch)} participants")

        for p in batch:
            demographics = p.get("demographics") or {}
            mdh_email = demographics.get("email", "").strip().lower()
            if mdh_email == email.strip().lower():
                print(f"[DEBUG] Found match for {email} → {p['participantIdentifier']} (page {page})")
                return p["participantIdentifier"]

        if len(batch) < 200:
            # last page
            break

        page += 1

    print(f"[WARN] Email {email} not found in MDH")
    return None


def send_mdh_notification(project_id, access_token, participant_identifier, notification_id):
    url = f"{MDH_BASE_URL}/api/v1/administration/projects/{project_id}/notifications"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = [{
        "participantIdentifier": participant_identifier,
        "notificationIdentifier": notification_id,
    }]
    print(f"[DEBUG] POST {url}")
    print(f"[DEBUG] Notification payload: {payload}")
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    print(f"[DEBUG] Notification response: status={r.status_code}, body={r.text[:500]}")
    r.raise_for_status()
    return r.status_code


def lambda_handler(event, context):
    project_id = os.environ["RKS_PROJECT_ID"].strip()
    access_token = get_service_access_token()

    # ---- Pull participants from DynamoDB ----
    participants = []
    last_evaluated_key = None
    while True:
        if last_evaluated_key:
            response = table.scan(ExclusiveStartKey=last_evaluated_key)
        else:
            response = table.scan()

        for item in response.get("Items", []):
            pid = item.get("id")
            email = item.get("UH_email")

            if isinstance(email, (set, list)):
                email = list(email)[0] if email else None
            if not isinstance(email, str) or not email.strip():
                continue
            if pid is not None and not isinstance(pid, str):
                pid = str(pid)

            participants.append({"id": pid, "email": email.strip()})

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    print(f"[INFO] Found {len(participants)} participants in DynamoDB")

    notified = 0
    emailed = 0
    skipped = 0

    for p in participants:
        pid = p["id"]
        uh_email = p["email"]

        # ---- Check UH inactivity ----
        timestamp_status = get_last_timestamp_status(base_url_uh, api_key, uh_email, stale_after=3)
        status = timestamp_status.get(uh_email)

        if not status:
            print(f"[WARN] No UH data returned for id={pid}, email={uh_email} — skipping")
            skipped += 1
            continue

        hours_ago = status["hours_ago"]
        print(f"[INFO] id={pid} last_ts={status['last_ts_utc']}, hours_ago={hours_ago}")

        # ---- >6h: SES email to study team ----
        if hours_ago == -1 or hours_ago > 6:
            print(f"[INFO] Sending inactivity email for id={pid} (hours_ago={hours_ago})")
            send_inactive_email(pid, uh_email, status["last_ts_utc"], hours_ago)
            emailed += 1

        # ---- >3h: MDH sync_reminder push notification ----
        if hours_ago == -1 or hours_ago > 3:
            mdh_email = f"glowup-{pid}@c4dhi.org"
            print(f"[INFO] Looking up MDH participant for {mdh_email}")
            mdh_participant_id = find_mdh_participant_by_email(project_id, access_token, mdh_email)

            if not mdh_participant_id:
                print(f"[WARN] No MDH participant found for {mdh_email} — skipping notification")
                skipped += 1
                continue

            try:
                status_code = send_mdh_notification(project_id, access_token, mdh_participant_id, "sync_reminder")
                print(f"[INFO] Sent sync_reminder to {mdh_participant_id} (id={pid}), status={status_code}")
                notified += 1
            except Exception as e:
                print(f"[ERROR] Failed to send notification to {mdh_participant_id} (id={pid}): {e}")
                skipped += 1
        else:
            print(f"[INFO] id={pid} is active ({hours_ago}h ago) — no notification needed")
            skipped += 1

    return {"processed": len(participants), "notified": notified, "emailed": emailed, "skipped": skipped}
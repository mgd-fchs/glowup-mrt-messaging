import os
import boto3
from api_utils import *

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("UH_emails")
ses = boto3.client("ses", region_name="eu-north-1")


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


def lambda_handler(event, context):
    participants = []  # list of dicts: {"id": ..., "email": ...}
    last_evaluated_key = None

    # go through dynamo DB to get all active participant IDs + emails
    while True:
        if last_evaluated_key:
            response = table.scan(ExclusiveStartKey=last_evaluated_key)
        else:
            response = table.scan()

        items = response.get("Items", [])
        for item in items:
            participant_id = item.get("id")  # <-- adjust if your attribute name differs
            email = item.get("UH_email")

            # Normalize set or list for email
            if isinstance(email, (set, list)):
                email = list(email)[0]

            if email is not None and not isinstance(email, str):
                raise ValueError(f"UH_email is not a string: {email} (type {type(email)})")

            if participant_id is not None and not isinstance(participant_id, str):
                # if you store numeric IDs, you can cast instead of raising:
                participant_id = str(participant_id)

            # Only keep rows with a usable email
            if email:
                participants.append({"id": participant_id, "email": email})

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    # check UH API for inactivity of each active participant
    for p in participants:
        mail = p["email"]
        pid = p.get("id")

        timestamp_status = get_last_timestamp_status(base_url_uh, api_key, mail, stale_after=6)

        status = timestamp_status.get(mail)
        if not status:
            print(f"UH API returned no data for {mail} (id={pid})")
            continue

        print(f"Participant id={pid}, email={mail} has timestamp status: {status}")

        is_inactive = status.get("stale", False)

        print(
            f"Participant id={pid}, email={mail} last updated UH at {status['last_ts_utc']}, "
            f"{status['hours_ago']} hours ago"
        )

        if is_inactive:
            print(f"Sending SES email for inactive participant id={pid}, email={mail}")
            send_inactive_email(
                pid,
                mail,
                status["last_ts_utc"],
                status["hours_ago"],
            )

    return {"processed": len(participants)}

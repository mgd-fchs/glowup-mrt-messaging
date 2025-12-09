import os
import boto3
from api_utils import *

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("UH_emails")
ses = boto3.client("ses")


def send_inactive_email(participant_email, last_ts, hours_ago):
    recipient_1 = os.environ["EMAIL_RECIPIENT_1"]
    recipient_2 = os.environ["EMAIL_RECIPIENT_2"]
    sender = os.environ["EMAIL_SENDER"]  # must be verified in SES

    print(f"Sending to {recipient_1}, {recipient_2}, {sender}")

    subject = f"UH participant inactive: {participant_email}"
    body_text = (
        f"The participant with email {participant_email} has not synchronized their Ultrahuman data.\n"
        f"Last timestamp: {last_ts}\n"
        f"Hours since last update: {hours_ago}\n"
        f"Please follow up with the participant."
    )

    ses.send_email(
        Source=sender,
        Destination={
            "ToAddresses": [recipient_1]
        },
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body_text}}
        }
    )


def lambda_handler(event, context):
    emails = []
    last_evaluated_key = None

    # go through dynamo DB to get all active e-mails
    while True:
        if last_evaluated_key:
            response = table.scan(ExclusiveStartKey=last_evaluated_key)
        else:
            response = table.scan()

        items = response.get("Items", [])
        for item in items:
            # Only collect if attribute exists
            email = item.get("UH_email")

            # Normalize set or list
            if isinstance(email, (set, list)):
                email = list(email)[0]

            if not isinstance(email, str):
                raise ValueError(f"UH_email is not a string: {email} (type {type(email)})")

            if email:
                emails.append(email)

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    # check UH API for inactivity of each active participant
    for mail in emails:
        timestamp_status = get_last_timestamp_status(base_url_uh, api_key, mail, stale_after=5)

        status = timestamp_status.get(mail)
        if not status:
            print(f"UH API returned no data for {mail}")
            continue

        print(f"Participant {mail} has timestamp status: {status}")

        is_inactive = status.get("stale", False)

        print(
            f"Participant {mail} last updated UH at {status['last_ts']}, "
            f"{status['hours_ago']} hours ago"
        )

        if is_inactive:
            print(f"Sending SES email for inactive participant {mail}")
            send_inactive_email(
                mail,
                status["last_ts"],
                status["hours_ago"]
            )

    return {"processed": len(emails)}

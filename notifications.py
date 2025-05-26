import requests

def send_notifications(service_access_token, project_id, participant_identifier, total_steps, threshold=6000, low_steps_notification_id="xxx", high_steps_notification_id="yyy"):
    """
    Sends a notification to a participant based on their daily step count.

    Parameters:
    - service_access_token: The API access token.
    - project_id: The ID of the MyDataHelps project.
    - participant_identifier: The unique identifier for the participant.
    - total_steps: The participant's total step count for the day.
    - threshold: The step count threshold to determine which notification to send.
    - low_steps_notification_id: The notification ID to send if steps are below the threshold.
    - high_steps_notification_id: The notification ID to send if steps are equal to or above the threshold.
    """

    low_steps_notification_id = "mood_neg_00"
    high_steps_notification_id = "mood_pos_00"
    # Determine which notification to send
    notification_id = low_steps_notification_id if total_steps < threshold else high_steps_notification_id

    # Set up the request
    url = f"https://mydatahelps.org/api/v1/administration/projects/{project_id}/notifications"
    headers = {
        "Authorization": f"Bearer {service_access_token}",
        "Content-Type": "application/json"
    }
    payload = [
        {
            "participantIdentifier": participant_identifier,
            "notificationIdentifier": notification_id,
            "sendTime": "2025-05-23T11:34:00Z"

        }
    ]

    # Send the notification
    response = requests.post(url, headers=headers, json=payload)

    # Check for successful request
    if response.status_code == 200:
        print(f"Notification '{notification_id}' sent to participant '{participant_identifier}'.")
    else:
        print(f"Failed to send notification. Status code: {response.status_code}, Response: {response.text}")

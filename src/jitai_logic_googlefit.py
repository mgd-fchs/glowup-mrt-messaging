import time
import os
import requests

from jitai_utils import *
from api_utils import *
from notifications import *
import google_fit as GoogleFit

def jitai_logic_googlefit():
    access_token = get_service_access_token()

    # Get all participants
    participants = []
    page = 0
    while True:
        url = f'api/v1/administration/projects/{project_id}/participants?pageNumber={page}&pageSize=100'
        response = get_from_api(access_token, url)
        data = response.json()
        participants.extend(data.get("participants", []))
        if len(data.get("participants", [])) < 100:
            break
        page += 1

    # Filter to those in active meal windows
    active_meal_window_participants = get_active_meal_window_participants(participants)
    print("Currently active:", active_meal_window_participants)

    if not active_meal_window_participants:
        print("No participants currently in a meal window.")
        return

    # Run for first active participant (loop over all if needed)
    p = active_meal_window_participants[0]
    print(p)
    pid = p["participantIdentifier"]

    # Query Google Fit steps
    steps_data = GoogleFit.get_google_fit_steps(
        access_token,
        project_id,
        pid,
        base_url
    )

    daily_steps = GoogleFit.aggregate_steps_by_source(steps_data)
    total_steps = sum(daily_steps.values())

    print("Step totals by source (today):")
    for source, count in daily_steps.items():
        print(f"{source}: {count} steps")
    print(f"Total: {total_steps} steps")

    # Optionally query Google Fit sleep
    sleep_data = GoogleFit.get_google_fit_sleep(
        access_token,
        project_id,
        pid,
        base_url
    )
    print(f"Retrieved {len(sleep_data)} Google Fit sleep entries.")

    # Send notification
    send_notifications(access_token, project_id, pid, total_steps)


if __name__ == "__main__":
    while True:
        print("Running JITAI loop (GoogleFit)...")
        jitai_logic_googlefit()
        time.sleep(300)  # wait 5 minutes

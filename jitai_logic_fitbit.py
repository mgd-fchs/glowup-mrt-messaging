import time
import os
import requests

from jitai_utils import *
from api_utils import *
from notifications import *
import fitbit as FitBit 

def jitai_logic_fitbit():
    access_token = get_service_access_token()

    # Get all participants
    participants = []
    page = 0
    while True:
        url = f'api/v1/administration/projects/{project_id}/participants'
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

    # Run for the first active participant (can expand later)
    p = active_meal_window_participants[0]
    pid = p["participantIdentifier"]

    # Get steps from Fitbit
    steps_data = FitBit.get_fitbit_steps(
        access_token,
        project_id,
        pid,
        base_url
    )

    daily_steps = FitBit.aggregate_steps_by_source(steps_data)
    total_steps = sum(daily_steps.values())

    print("Step totals by source (today):")
    for source, count in daily_steps.items():
        print(f"{source}: {count} steps")

    print(f"Total: {total_steps} steps")

    # Optional: Get sleep data from Fitbit
    sleep_data = FitBit.get_fitbit_sleep(
        access_token,
        project_id,
        pid,
        base_url
    )

    print(f"Retrieved {len(sleep_data)} sleep entries.")

    # Send the appropriate notification
    send_notifications(access_token, project_id, pid, total_steps)


if __name__ == "__main__":
    while True:
        print("Running JITAI loop (Fitbit)...")
        jitai_logic_fitbit()
        time.sleep(300)

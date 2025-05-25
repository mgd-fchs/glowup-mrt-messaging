import time
import os
import time
import requests

from jitai_utils import *
from api_utils import *
from notifications import *
import apple_health as AppleHealth

def jitai_logic_applehealth():
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
    # print(participants)

    # Participants with active meal windows
    active_meal_window_participants = get_active_meal_window_participants(participants)
    print("Currently active:", active_meal_window_participants)

    # Query steps
    # TODO: Wrap this in loop for all active participants
    steps_data = AppleHealth.get_apple_health_steps(
        access_token,
        project_id,
        # active_meal_window_participants[0]["participantIdentifier"],
        "MDH-0274-8346",
        base_url
    )

    daily_steps = AppleHealth.aggregate_steps_by_source(steps_data)

    print("Step totals by source (today):")
    for source, total in daily_steps.items():
        print(f"{source}: {total} steps")

        #TODO: If two sources, use UH/Fitbit, else use whichever available (could be phone)

    # # Query sleep   # TODO: Fix this
    # sleep_data = AppleHealth.get_apple_health_sleep(
    #     service_access_token=access_token,
    #     project_id=project_id,
    #     participant_identifier=active_meal_window_participants[0]["participantIdentifier"],
    #     base_url=base_url
    # )

    # for entry in sleep_data:
    #     print(entry)

    # Example va
    total_steps = 5500  # Example step count

    # Send the appropriate notification based on step count
    send_notifications(access_token, project_id, "MDH-0274-8346", total_steps)


if __name__ == "__main__":
    while True:
        print("Running JITAI loop...")
        jitai_logic_applehealth()
        time.sleep(300)  # wait 5 minutes

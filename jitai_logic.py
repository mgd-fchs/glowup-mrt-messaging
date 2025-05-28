import time
import os
import time
import requests

from jitai_utils import *
from api_utils import *
from notifications import *

import apple_health as AppleHealth
import health_connect as HealthConnect
import google_fit as GoogleFit
import fitbit as Fitbit


def lambda_handler(event, context):
    print("Running MRT loop...")

    segment_ids = {
        "iOS": "fd09bd40-a26b-42b3-86af-4a59cbba489a",
        "Android": "2c3457ae-3c5b-4616-8480-e1e4ac750cdd",
        "Fitbit": "e1fc5eaf-e279-4e83-8a05-69831c352bd1"
    }

    access_token = get_service_access_token()

    active_participant_ids_by_platform = {}

    for platform, seg_id in segment_ids.items():
        segment_participants = get_participants_by_segment(project_id, access_token, seg_id)
        active_participants = get_active_meal_window_participants(segment_participants)
        active_ids = [p["participantIdentifier"] for p in active_participants]
        active_participant_ids_by_platform[platform] = active_ids
        print(f"{platform} - Active participant IDs: {active_ids}")

    # Query steps and sleep per participant
    for platform, participant_ids in active_participant_ids_by_platform.items():
        for pid in participant_ids:
            if platform == "iOS":
                steps_data = AppleHealth.get_steps(access_token, project_id, pid, base_url)
                sleep_data = AppleHealth.get_sleep(access_token, project_id, pid, base_url)
                daily_steps = AppleHealth.aggregate_steps_by_source(steps_data)
            elif platform == "Android":
                steps_data = GoogleFit.get_steps(access_token, project_id, pid, base_url)
                sleep_data = GoogleFit.get_sleep(access_token, project_id, pid, base_url)
                daily_steps = GoogleFit.aggregate_steps_by_source(steps_data)
            elif platform == "Fitbit":
                steps_data = Fitbit.get_steps(access_token, project_id, pid, base_url)
                sleep_data = Fitbit.get_sleep(access_token, project_id, pid, base_url)
                daily_steps = Fitbit.aggregate_steps_by_source(steps_data)
            
            print(f"{platform} - {pid} - Daily steps by source:", daily_steps)
            print(f"{platform} - {pid} - Sleep data:", sleep_data)

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

    total_steps = 5500  # Example step count

    # Send the appropriate notification based on step count
    send_notifications(access_token, project_id, "MDH-0274-8346", total_steps)

    return {"status": "completed"}


if __name__ == "__main__":
    while True:
        lambda_handler("fz", "cd")
        time.sleep(300)  # wait 5 minutes

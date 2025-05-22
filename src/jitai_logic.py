import time
import os
from api_utils import get_service_access_token, get_from_api, base_url, project_id
from jitai_utils import *
import time
import requests

def jitai_logic():
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
    print(participants)

    active_meal_window_participants = get_active_meal_window_participants(participants)
    print("Currently active:", active_meal_window_participants)


if __name__ == "__main__":
    while True:
        print("Running JITAI loop...")
        jitai_logic()
        time.sleep(300)  # wait 5 minutes

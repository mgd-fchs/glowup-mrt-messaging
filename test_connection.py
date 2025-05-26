# run_all.py

import os
from api_utils import get_service_access_token, get_from_api, get_participant_access_token

# Get a service access token
service_access_token = get_service_access_token()
os.environ["ACCESS_TOKEN"] = service_access_token
print(os.environ["ACCESS_TOKEN"])

# Example API call
url = f'api/v1/administration/projects/{os.getenv("RKS_PROJECT_ID")}/participants'
response = get_from_api(service_access_token, url)
print(response.json())

# rks_api_utils.py

from datetime import datetime
from uuid import uuid4
import os
from typing import Optional, Dict
import jwt  # pip install PyJWT
import requests  # pip install requests
from dotenv import load_dotenv  # pip install python-dotenv

load_dotenv()

# Read environment variables
private_key = os.getenv('RKS_PRIVATE_KEY')
service_account_name = os.getenv('RKS_SERVICE_ACCOUNT')
project_id = os.getenv('RKS_PROJECT_ID')
base_url = os.getenv('BASE_URL')
token_url = f'{base_url}/identityserver/connect/token'


def get_service_access_token() -> str:
    assertion = {
        "iss": service_account_name,
        "sub": service_account_name,
        "aud": token_url,
        "exp": datetime.now().timestamp() + 200,
        "jti": str(uuid4()),
    }
    signed_assertion = jwt.encode(payload=assertion, key=private_key, algorithm="RS256")
    token_payload = {
        "scope": "api",
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": signed_assertion
    }
    response = requests.post(url=token_url, data=token_payload)
    response.raise_for_status()
    return response.json()["access_token"]


def get_from_api(
    service_access_token: str,
    resource_url: str,
    query_params: Optional[Dict[str, str]] = None,
    raise_error: bool = True
) -> requests.Response:
    if query_params is None:
        query_params = {}

    headers = {
        "Authorization": f'Bearer {service_access_token}',
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8"
    }

    url = f'{base_url}/{resource_url}'
    response = requests.get(url=url, params=query_params, headers=headers)

    if raise_error:
        response.raise_for_status()

    return response


def get_participant_access_token(
    service_access_token: str,
    participant_id: str,
    scopes: str
) -> str:
    token_payload = {
        "scope": scopes,
        "grant_type": "delegated_participant",
        "participant_id": participant_id,
        "client_id": "MyDataHelps.DelegatedParticipant",
        "client_secret": "secret",
        "token": service_access_token,
    }
    response = requests.post(url=token_url, data=token_payload)
    response.raise_for_status()
    return response.json()["access_token"]

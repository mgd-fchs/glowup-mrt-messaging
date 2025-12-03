# rks_api_utils.py

from datetime import datetime, timedelta, date
from uuid import uuid4
import os
from typing import Optional, Dict
import jwt  
import requests 
from dateutil import parser
from dotenv import load_dotenv

load_dotenv()

# Read environment variables
private_key = os.getenv('RKS_PRIVATE_KEY')
service_account_name = os.getenv('RKS_SERVICE_ACCOUNT')
project_id = os.getenv('RKS_PROJECT_ID')
base_url = os.getenv('BASE_URL')
token_url = f'{base_url}/identityserver/connect/token'
base_url_uh = os.getenv('BASE_URL_UH')
api_key = os.getenv('UH_API_TOKEN')

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


def safe_parse_iso(s):
    try:
        return parser.isoparse(s)
    except Exception as e:
        print(f"Skipping invalid timestamp: {s} – {e}")
        return None


def get_all_participants(project_id, access_token):
    """
    Fetches all participants in the given MyDataHelps project.

    Returns:
        list of participant objects.
    """
    url = f"https://mydatahelps.org/api/v1/administration/projects/{project_id}/participants"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to fetch participants: {response.status_code} - {response.text}")

    return response.json().get("participants", [])

def get_surveys(project_id, access_token, participant_id):

    url = f"https://mydatahelps.org/api/v1/administration/projects/{project_id}/participants/{participant_id}/surveyevents"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to fetch surveys for {participant_id}: {response.status_code} - {response.text}")

    return response.json().get("surveyEvents", [])


def fetch_metrics(base_url_uh, api_token, email, day_delta):
    # day_delta = 1 for yesterday, =0 for today, etc.
    yesterday = date.today() - timedelta(days=day_delta)
    date_str = yesterday.strftime("%d/%m/%Y")   # UH expects DD/MM/YYYY

    params = {"email": email, "date": date_str}
    headers = {"Authorization": api_token}

    resp = requests.get(base_url_uh, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json()


def extract_metric_data(api_response):
    if not isinstance(api_response, dict):
        return []
    data = api_response.get("data")
    if not isinstance(data, dict):
        return []
    metric_data = data.get("metric_data")
    if not isinstance(metric_data, list):
        return []
    return metric_data


def get_last_timestamp_status(base_url_uh, api_token, participant_email):
    now = datetime.now()
    six_hours_ago_ts = int((now - timedelta(hours=6)).timestamp())

    today = date.today()
    yesterday = today - timedelta(days=1)

    # UH requires DD/MM/YYYY
    dates = [
        0, 1
    ]

    results = {}


    timestamps = []

    for d in dates:
        try:
            resp = fetch_metrics(base_url_uh, api_token, participant_email, d)
            metrics = extract_metric_data(resp)

            for m in metrics:
                if not isinstance(m, dict):
                    continue

                obj = m.get("object", {})
                if not isinstance(obj, dict):
                    continue

                # Only use detailed values with real datapoints
                vals = obj.get("values")
                if isinstance(vals, list):
                    for item in vals:
                        if not isinstance(item, dict):
                            continue

                        val = item.get("value")
                        ts = item.get("timestamp")

                        # Skip empty or None-value datapoints
                        if val in (None, "", [], {}):
                            continue

                        if isinstance(ts, (int, float)):
                            timestamps.append(ts)

        except Exception:
            pass

    if timestamps:
        last_ts = max(timestamps)
        hours_ago = (now.timestamp() - last_ts) / 3600.0
        stale = last_ts < six_hours_ago_ts
    else:
        last_ts = None
        hours_ago = None
        stale = True  # no data → stale

    results[participant_email] = {
        "last_ts": last_ts,
        "hours_ago": hours_ago,
        "stale": stale,
    }

    return results

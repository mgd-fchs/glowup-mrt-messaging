# rks_api_utils.py

from datetime import datetime, timedelta, date, timezone
from uuid import uuid4
import os
from typing import Optional, Dict
import jwt  
import requests 
import traceback
from dateutil import parser
import pandas as pd
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


def get_last_timestamp_status(base_url_uh, api_token, participant_email, stale_after=6):
    now = datetime.now(timezone.utc)
    six_hours_ago_ts = int((now - timedelta(hours=stale_after)).timestamp())

    # Query today (0), and last 5 days (1–5)
    dates = list(range(0, 6))
    results = {}
    timestamps = []

    for d in dates:
        try:
            resp = fetch_metrics(base_url_uh, api_token, participant_email, d)
            metrics = extract_metric_data(resp)

            for m in metrics:
                obj = m.get("object", {})
                if not isinstance(obj, dict):
                    continue

                vals = obj.get("values")
                if not isinstance(vals, list):
                    continue

                for item in vals:
                    ts = item.get("timestamp")
                    if isinstance(ts, (int, float)):
                        timestamps.append(ts)

        except Exception as e:
            print(
                f"Error while fetching/parsing metrics "
                f"email={participant_email} day_delta={d} "
                f"type={type(e).__name__} message={e}"
            )
            traceback.print_exc()

    if timestamps:
        last_ts = max(timestamps)
        dt = datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M:%S")
        hours_ago = (now.timestamp() - last_ts) / 3600.0
        stale = last_ts < six_hours_ago_ts
    else:
        last_ts = -1
        dt = -1
        hours_ago = -1
        stale = True

    results[participant_email] = {
        "last_ts_utc": dt,
        "hours_ago": int(hours_ago),
        "stale": stale,
    }

    return results

def get_participant_id_and_email(
    service_access_token: str,
    base_url: str,
    project_id: str,
    page_size: int = 100
):
    """Fetch participantIdentifier + email for all participants."""
    out = []
    page_number = 0

    while True:
        query_params = {
            "pageNumber": page_number,
            "pageSize": page_size,
            "sortBy": "InsertedDate",
            "sortAscending": "true"
        }

        response = get_from_api(
            base_url,
            service_access_token=service_access_token,
            resource_url=f"api/v1/administration/projects/{project_id}/participants",
            query_params=query_params,
            raise_error=True
        )
        items = response.json().get("participants", [])
        if not items:
            break

        for p in items:
            demo = p.get("demographics") or {}   # can be null
            out.append({
                "participantIdentifier": p.get("participantIdentifier"),
                "id": p.get("id"),
                "email": demo.get("email"),
            })

        if len(items) < page_size:
            break
        page_number += 1

    return out

def get_survey_tasks(access_token, base_url, project_id, page_size=100, **filters):
    """Fetch survey tasks, following nextPageID cursor pagination."""
    tasks, page_id, seen = [], None, set()

    while True:
        params = {"pageSize": page_size, **filters}
        if page_id:
            params["pageID"] = page_id

        response = get_from_api(
            base_url,
            service_access_token=access_token,
            resource_url=f"api/v1/administration/projects/{project_id}/surveytasks",
            query_params=params,
            raise_error=True
        )
        body = response.json()
        tasks.extend(body.get("surveyTasks", []))

        page_id = body.get("nextPageID")
        if not page_id or page_id in seen:   # guard against a repeating cursor
            break
        seen.add(page_id)

    return tasks

def _fetch_tasks(base_url, project_id, access_token, identifiers, **filters):
    """Page surveytasks per participant instead of pulling the whole project."""
    url = f"{base_url}/api/v1/administration/projects/{project_id}/surveytasks"
    tasks = []
    with requests.Session() as s:
        s.headers.update({"Authorization": f"Bearer {access_token}",
                          "Accept": "application/json"})
        for pid in identifiers:
            page_id = None
            while True:
                params = {"pageSize": 200, "participantIdentifier": pid, **filters}
                if page_id:
                    params["pageID"] = page_id
                r = s.get(url, params=params)
                r.raise_for_status()
                body = r.json()
                tasks.extend(body.get("surveyTasks", []))
                page_id = body.get("nextPageID")
                if not page_id:
                    break
    return tasks


def _fetch_snack_answers(base_url, project_id, access_token, identifiers, snack):
    """Same scoping for surveyanswers — usually the bigger of the two pulls."""
    url = f"{base_url}/api/v1/administration/projects/{project_id}/surveyanswers"
    answers = []
    with requests.Session() as s:
        s.headers.update({"Authorization": f"Bearer {access_token}",
                          "Accept": "application/json"})
        for pid in identifiers:
            page_id = None
            while True:
                params = {"pageSize": 200, "surveyName": snack,
                          "participantIdentifier": pid}
                if page_id:
                    params["pageID"] = page_id
                r = s.get(url, params=params)
                r.raise_for_status()
                body = r.json()
                answers.extend(body.get("surveyAnswers", []))
                page_id = body.get("nextPageID")
                if not page_id:
                    break
    return answers


def snack_days(answers, first_meal, window_days):
    """One row per participant-day with >=1 snack submission, inside the window."""
    cols = ["participantIdentifier", "date", "snacks_per_day"]
    if not answers:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(answers)
    df = df.dropna(subset=["participantIdentifier", "surveyResultID", "date"])
    df["date"] = pd.to_datetime(df["date"], utc=True, format="mixed",
                                errors="coerce").dt.date
    df = df.dropna(subset=["date"]).drop_duplicates(subset=["surveyResultID"])

    df = df.merge(first_meal, on="participantIdentifier", how="inner")
    df = df[(df["date"] >= df["first_meal_date"]) &
            (df["date"] <= df["first_meal_date"] + timedelta(days=window_days - 1))]

    return (df.groupby(["participantIdentifier", "date"], as_index=False)
              .size().rename(columns={"size": "snacks_per_day"}))


def meal_day_counts(base_url, project_id, access_token, recent_ids, meals,
                    window_days):
    all_tasks = _fetch_tasks(base_url, project_id, access_token, recent_ids)
    if not all_tasks:
        return pd.DataFrame(columns=["participantIdentifier", "first_meal_date",
                                     "days_total", "days_2plus_meals",
                                     "days_2plus_meals_pct", "days_2plus_any",
                                     "days_2plus_any_pct"])

    df = pd.DataFrame(all_tasks)[["participantIdentifier", "surveyName", "status", "endDate"]]
    df = df[df["participantIdentifier"].isin(set(recent_ids))]   # belt-and-braces
    df["date"] = pd.to_datetime(df["endDate"], utc=True, format="mixed",
                                errors="coerce").dt.date
    df = df.dropna(subset=["date"])

    first_meal = (df[df["surveyName"].isin(meals)]
                  .groupby("participantIdentifier", as_index=False)["date"]
                  .min().rename(columns={"date": "first_meal_date"}))

    done = df[(df["status"].str.lower() == "complete") & (df["surveyName"].isin(meals))]
    done = done.merge(first_meal, on="participantIdentifier", how="inner")
    done = done[(done["date"] >= done["first_meal_date"]) &
                (done["date"] <= done["first_meal_date"] + timedelta(days=window_days - 1))]

    meals_per_day = (done.groupby(["participantIdentifier", "date"])["surveyName"]
                     .nunique().reset_index(name="meal_count"))

    answers = _fetch_snack_answers(base_url, project_id, access_token, recent_ids)
    snacks = snack_days(answers, first_meal, window_days)

    daily = (meals_per_day.merge(snacks, on=["participantIdentifier", "date"], how="outer")
             .fillna({"meal_count": 0, "snacks_per_day": 0}))
    daily["any_count"] = daily["meal_count"] + daily["snacks_per_day"]

    out = (daily.assign(m=daily["meal_count"] >= 2, a=daily["any_count"] >= 2)
           .groupby("participantIdentifier", as_index=False)
           .agg(days_2plus_meals=("m", "sum"), days_2plus_any=("a", "sum")))

    out = first_meal.merge(out, on="participantIdentifier", how="left").fillna(0)
    out["days_total"] = window_days
    for c in ("days_2plus_meals", "days_2plus_any"):
        out[c] = out[c].astype(int)
        out[f"{c}_pct"] = (out[c] / window_days * 100).round(1)

    return out[["participantIdentifier", "first_meal_date", "days_total",
                "days_2plus_meals", "days_2plus_meals_pct",
                "days_2plus_any", "days_2plus_any_pct"]]

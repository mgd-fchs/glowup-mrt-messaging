import os, csv, json, requests, boto3
import pandas as pd
from dateutil import parser
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo
from datetime import datetime, timezone, timedelta
from api_utils import *
from decimal import Decimal


load_dotenv()
# -------------------------
# Config
# -------------------------
BASE_URL_UH = "https://partner.ultrahuman.com/api/v1/metrics"
API_TOKEN = os.environ["UH_API_TOKEN"].strip()

DDB_TABLE = os.environ.get("DDB_TABLE", "UH_emails")
DDB_REGION = os.environ.get("AWS_REGION", "eu-north-1")  # Lambda sets this; locally set it or rely on config

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/tmp/uh_weekly_output")  # local runs
# In Lambda you can set OUTPUT_DIR="/tmp/uh_weekly_output"

# Optionally upload to S3 (leave unset to disable)
S3_BUCKET = os.environ.get("S3_BUCKET", "glowup-mdh")
S3_PREFIX = os.environ.get("S3_PREFIX", "uh_weekly_exports/")  # folder prefix

RKS_PROJECT_ID = os.environ.get("RKS_PROJECT_ID") 
BASE_URL = os.environ.get("BASE_URL") 
ses = boto3.client("ses", region_name="eu-north-1")

# constants for adherence comp
WINDOW_DAYS = 28
MEALS = {"log_breakfast_de", "log_lunch_de", "log_dinner_de"}
SNACK = "log_snack_de"
T1 = "T1-improved-de"
T3 = "t3-followup"

# -------------------------
# DynamoDB: get participants
# -------------------------
dynamodb = boto3.resource("dynamodb", region_name=DDB_REGION) if DDB_REGION else boto3.resource("dynamodb")
table = dynamodb.Table(DDB_TABLE)
ADHERENCE_TABLE = os.environ.get("ADHERENCE_TABLE", "Glowup-adherence")
adherence_table = dynamodb.Table(ADHERENCE_TABLE)


def get_participants_from_ddb():
    """Returns [{"id": "...", "email": "..."}, ...]"""
    participants = []
    last_evaluated_key = None

    while True:
        if last_evaluated_key:
            resp = table.scan(ExclusiveStartKey=last_evaluated_key)
        else:
            resp = table.scan()

        for item in resp.get("Items", []):
            pid = item.get("id")
            email = item.get("UH_email")

            # normalize email if it's a set/list
            if isinstance(email, (set, list)):
                email = list(email)[0] if email else None

            if not isinstance(email, str) or not email.strip():
                continue

            participants.append(
                {
                    "id": str(pid) if pid is not None else None,
                    "email": email.strip(),
                }
            )

        last_evaluated_key = resp.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    return participants


# -------------------------
# UH API helpers
# -------------------------
def fetch_metrics_for_date(email: str, date_obj: datetime.date) -> dict:
    date_str = date_obj.strftime("%d/%m/%Y")  # UH expects DD/MM/YYYY
    params = {"email": email, "date": date_str}
    headers = {"Authorization": API_TOKEN}

    r = requests.get(BASE_URL_UH, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


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


def has_real_data(metric_entry):
    if not isinstance(metric_entry, dict):
        return False
    obj = metric_entry.get("object")
    if not isinstance(obj, dict):
        return False

    if obj.get("value") not in (None, "", []):
        return True

    values = obj.get("values")
    if isinstance(values, list) and len(values) > 0:
        return True

    return False


def filter_non_empty(metric_data):
    if not isinstance(metric_data, list):
        return []
    out = []
    for m in metric_data:
        if not isinstance(m, dict):
            continue
        if m.get("type") == "vo2_max":
            continue
        if has_real_data(m):
            out.append(m)
    return out


def expand_metric_to_rows(pid: str, email: str, date_str: str, metric: dict):
    """Convert one metric object into 0..N long-format rows."""
    rows = []
    metric_type = metric.get("type", "")
    obj = metric.get("object", {}) or {}

    if metric_type == "vo2_max":
        return rows

    values = obj.get("values")
    if isinstance(values, list) and len(values) > 0:
        for item in values:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "id": pid,
                    "email": email,
                    "date": date_str,  # DD/MM/YYYY
                    "metric_type": metric_type,
                    "timestamp": item.get("timestamp"),
                    "value": item.get("value"),
                }
            )
        return rows

    if obj.get("value") not in (None, "", []):
        rows.append(
            {
                "id": pid,
                "email": email,
                "date": date_str,
                "metric_type": metric_type,
                "timestamp": obj.get("day_start_timestamp", ""),
                "value": obj.get("value"),
            }
        )
        return rows

    return rows


def metrics_to_rows(pid: str, email: str, date_str: str, metrics: list):
    all_rows = []
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        all_rows.extend(expand_metric_to_rows(pid, email, date_str, metric))
    return all_rows


# -------------------------
# Weekly export
# -------------------------
def week_label(date_obj: datetime.date) -> str:
    iso_year, iso_week, _ = date_obj.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def export_last_day_per_participant(participants, end_day_delta: int = 1):
    """
    (MINIMAL CHANGE) Now creates ONE JSON per participant for ONLY yesterday's calendar date.

    Writes to: OUTPUT_DIR/<ID>/<YYYY-MM-DD>__id_<ID>.json

    The JSON contains the FULL Ultrahuman API response for that date (no parsing/filtering),
    plus minimal metadata (id, email, date, pulled_at_utc).
    """
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        tz = ZoneInfo("Europe/Vienna")
        target_date = datetime.now(tz).date() - timedelta(days=end_day_delta)
    except Exception:
        # fallback: previous behavior (UTC-based) if zoneinfo not available
        target_date = datetime.now(timezone.utc).date() - timedelta(days=end_day_delta)

    ymd = target_date.isoformat()  # YYYY-MM-DD

    created_files = []

    for p in participants:
        pid = str(p.get("id") or "NOID").strip()
        email = p["email"]

        # folder name must be just the ID
        safe_id = pid.replace("/", "_").replace("\\", "_").replace(" ", "_")
        participant_dir = os.path.join(OUTPUT_DIR, safe_id)
        os.makedirs(participant_dir, exist_ok=True)

        error = None
        resp = None

        try:
            resp = fetch_metrics_for_date(email, target_date)
        except Exception as e:
            error = str(e)

        # filename: date + id
        filename = f"{ymd}__id_{safe_id}.json"
        filepath = os.path.join(participant_dir, filename)

        out_obj = {
            "id": pid,
            "email": email,
            "date": ymd,
            "pulled_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "ultrahuman_response": resp,   # FULL JSON payload (includes sleep, etc.)
            "error": error,               # None if ok
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(out_obj, f, ensure_ascii=False)

        created_files.append(filepath)

    return {
        "date": ymd,
        "files": created_files,
    }


def upload_to_s3(filepaths):
    print("[DEBUG] upload_to_s3 called")
    print("[DEBUG] S3_BUCKET (global):", repr(S3_BUCKET))
    print("[DEBUG] Number of files passed in:", len(filepaths))

    if not S3_BUCKET:
        print("[ERROR] S3_BUCKET is empty or falsy → returning without upload")
        return []

    s3 = boto3.client("s3")
    uploaded = []

    for path in filepaths:
        print("[DEBUG] Processing local file:", path)

        if not os.path.exists(path):
            print("[ERROR] Local file does not exist:", path)
            continue

        filename = os.path.basename(path)

        participant_id = "UNKNOWN"
        parts = filename.split("__")
        if len(parts) >= 2 and parts[1].startswith("id_"):
            participant_id = parts[1][3:]
            participant_id = os.path.splitext(participant_id)[0]

        key = f"ultrahuman_database/{participant_id}/{filename}"
        print("[DEBUG] Upload target:", f"s3://{S3_BUCKET}/{key}")

        try:
            s3.upload_file(path, S3_BUCKET, key)
            print("[INFO] Upload succeeded:", key)
            uploaded.append({"bucket": S3_BUCKET, "key": key})
        except Exception as e:
            print("[ERROR] Upload failed for", key, "→", str(e))
            raise

    print("[DEBUG] upload_to_s3 finished, uploaded:", len(uploaded))
    return uploaded


def get_snack_completion(base_url, project_id, access_token, first_meal):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    url = f"{base_url}/api/v1/administration/projects/{project_id}/surveyanswers"
    params = {"limit": 200, "surveyName": "log_snack_de"}

    all_answers, page_id = [], None

    while True:
        if page_id:
            params["pageID"] = page_id

        r = requests.get(url, headers=headers, params=params)
        if r.status_code != 200:
            print(f"Failed: {r.status_code}, {r.text[:200]}")
            break

        data = r.json()
        all_answers.extend(data.get("surveyAnswers", []))
        page_id = data.get("nextPageID")
        if not page_id:
            break

    print(f"[INFO] Retrieved {len(all_answers)} raw snack answers")

    if not all_answers:
        return pd.DataFrame(columns=["participantIdentifier", "date", "snacks_per_day"])

    df = pd.DataFrame(all_answers)

    # Check required fields
    if not all(col in df.columns for col in ["participantIdentifier", "surveyResultID", "date"]):
        raise KeyError("Missing one of required columns: participantIdentifier, surveyResultID, date")

    # Drop missing
    df = df.dropna(subset=["participantIdentifier", "surveyResultID", "date"])

    # Parse timestamp and extract only date (YYYY-MM-DD)
    def extract_date(x):
        try:
            return parser.isoparse(x).date()
        except Exception:
            return None

    df["date"] = df["date"].apply(extract_date)
    df = df.dropna(subset=["date"])

    # Merge with first_meal to get participant-specific start date
    merged = pd.merge(df, first_meal, on="participantIdentifier", how="left")

    # Keep only snacks within 30 days after first meal
    merged = merged[
        merged["date"] <= (merged["first_meal_date"] + pd.to_timedelta(30, unit="d"))
    ]

    # Drop duplicate surveyResultIDs
    merged_unique = merged.drop_duplicates(subset=["surveyResultID"])

    # Count snacks per participant per day
    df_snack_daily = (
        merged_unique.groupby(["participantIdentifier", "date"], as_index=False)
        .size()
        .rename(columns={"size": "snacks_per_day"})
    )

    print(f"[INFO] Produced {len(df_snack_daily)} participant-day rows")
    return df_snack_daily

def build_recent_participants(base_url, project_id, access_token, weeks=3):
    """Participants who completed T3 in the last `weeks` weeks, with study start/end dates."""
    cols = ["participantIdentifier", "participantID", "email",
            "study_start_date", "study_end_date", "study_duration_days"]

    participants = pd.DataFrame(get_participant_id_and_email(access_token, base_url, project_id))

    raw = []
    for name in (T1, T3):
        raw += get_survey_tasks(access_token, base_url, project_id,
                                surveyName=name, status="complete")
    if not raw or participants.empty:
        print("[INFO] No T1/T3 tasks or participants found")
        return pd.DataFrame(columns=cols)

    tasks = pd.DataFrame(raw)
    tasks["completedDate"] = pd.to_datetime(tasks["modifiedDate"], utc=True, format="mixed")
    tasks["surveyKey"] = tasks["surveyName"].str.lower()
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(weeks=weeks)

    # T3 completions in the window -> eligibility + study_end_date
    t3 = tasks[(tasks["surveyKey"] == T3.lower()) & (tasks["completedDate"] >= cutoff)]
    t3 = (t3.groupby("participantID", as_index=False)["completedDate"].max()
            .rename(columns={"completedDate": "study_end_date"}))
    if t3.empty:
        print(f"[INFO] No T3 completions in the last {weeks} weeks")
        return pd.DataFrame(columns=cols)

    # earliest T1 completion (any time) -> study_start_date
    t1 = tasks[tasks["surveyKey"] == T1.lower()]
    t1 = (t1.groupby("participantID", as_index=False)["completedDate"].min()
            .rename(columns={"completedDate": "study_start_date"}))

    result = (t3.merge(t1, on="participantID", how="left")
                .merge(participants.rename(columns={"id": "participantID"}),
                       on="participantID", how="left"))
    result["study_duration_days"] = (result["study_end_date"] - result["study_start_date"]).dt.days

    print(f"[INFO] {len(result)} recent participants (T3 completed in last {weeks} weeks)")
    return result[cols]

def mdh_list_participants_with_metadata(base_url, project_id, access_token, limit=200):
    """
    Returns list of dicts with:
      participantIdentifier
      enrollmentDate
      demographics.email
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    url = f"{base_url}/api/v1/administration/projects/{project_id}/participants"
    params = {"limit": limit}

    all_participants = []
    page_id = None

    while True:
        if page_id:
            params["pageID"] = page_id

        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        batch = data.get("participants", [])
        all_participants.extend(batch)

        page_id = data.get("nextPageID")
        if not page_id:
            break

    return all_participants


def mdh_get_participant_detail(base_url, project_id, access_token, participant_identifier):
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    url = f"{base_url}/api/v1/administration/projects/{project_id}/participants/{participant_identifier}"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def mdh_build_participant_email_df(base_url, project_id, access_token, participant_identifiers):
    """
    Keep ONLY participants enrolled within the last 3 months.
    Pulls enrollmentDate + demographics.email from the PARTICIPANT DETAIL endpoint.
    """
    cutoff = datetime.now(timezone.utc) - relativedelta(months=3)

    rows = []
    dropped_old = 0
    missing_enroll = 0
    missing_email = 0
    errors = 0

    for pid in participant_identifiers:
        try:
            detail = mdh_get_participant_detail(base_url, project_id, access_token, pid)

            enrollment_raw = detail.get("enrollmentDate")
            if not enrollment_raw:
                missing_enroll += 1
                continue

            enrollment_dt = parser.isoparse(enrollment_raw)

            # KEEP only within last 3 months
            if enrollment_dt < cutoff:
                dropped_old += 1
                continue

            demographics = detail.get("demographics", {})
            email = demographics.get("email") if isinstance(demographics, dict) else None
            email = email.strip() if isinstance(email, str) else None
            if not email:
                missing_email += 1

            rows.append(
                {
                    "participantIdentifier": pid,
                    "email": email,
                    "enrollmentDate": enrollment_dt.date().isoformat(),
                }
            )

        except Exception as e:
            errors += 1
            rows.append(
                {
                    "participantIdentifier": pid,
                    "email": None,
                    "enrollmentDate": None,
                    "error": str(e),
                }
            )

    print("[DEBUG] cutoff:", cutoff.isoformat())
    print("[DEBUG] input participantIdentifiers:", len(participant_identifiers))
    print("[DEBUG] kept within last 3 months:", sum(1 for r in rows if r.get("enrollmentDate")))
    print("[DEBUG] dropped old:", dropped_old, "missing_enroll:", missing_enroll, "missing_email:", missing_email, "errors:", errors)

    return pd.DataFrame(rows)

def build_adherence_final(adherence, recent_participants):
    adherence_final = (
        adherence
        .merge(recent_participants[["participantIdentifier", "email"]],
               on="participantIdentifier", how="left")
        [["participantIdentifier", "email", "days_2plus_any", "days_2plus_any_pct"]]
        .rename(columns={
            "participantIdentifier": "participantIdentifier_MDH",
            "days_2plus_any": "adherent_days",
            "days_2plus_any_pct": "adherence_pct",
        })
    )

    # astype("string") so .str works even if all emails are NaN or the frame is empty
    adherence_final["Glowup-ID"] = (
        adherence_final["email"].astype("string")
        .str.extract(r"glowup-(\d+)")[0]
        .astype("Int64")
    )

    adherence_final = (
        adherence_final[["Glowup-ID", "email", "participantIdentifier_MDH",
                         "adherent_days", "adherence_pct"]]
        .sort_values("adherence_pct", ascending=False, ignore_index=True)
    )

    print(
        f"[INFO] adherence rows: {len(adherence_final)}, "
        f"missing email: {adherence_final['email'].isna().sum()}, "
        f"missing Glowup-ID: {adherence_final['Glowup-ID'].isna().sum()}"
    )
    return adherence_final

def write_adherence_to_ddb(adherence_final):
    """Upsert latest adherence per Glowup-ID (overwrites previous row)."""
    df = adherence_final.dropna(subset=["Glowup-ID"])
    updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    n = 0
    # overwrite_by_pkeys: if the same Glowup-ID appears twice, keep the last instead of erroring
    with adherence_table.batch_writer(overwrite_by_pkeys=["Glowup-ID"]) as batch:
        for r in df.to_dict("records"):
            item = {
                "Glowup-ID": str(int(r["Glowup-ID"])),                   # table key is String
                "participantIdentifier_MDH": str(r["participantIdentifier_MDH"]),
                "adherent_days": int(r["adherent_days"]),
                "adherence_pct": Decimal(str(round(float(r["adherence_pct"]), 1))),  # boto3 rejects floats
                "window_days": WINDOW_DAYS,
                "updated_at": updated_at,
            }
            if pd.notna(r["email"]):
                item["email"] = str(r["email"])
            batch.put_item(Item=item)
            n += 1

    print(f"[INFO] Wrote {n} adherence rows to {ADHERENCE_TABLE} "
          f"(skipped {len(adherence_final) - n} without Glowup-ID)")
    return n

# -------------------------
# Lambda entry point
# -------------------------
def lambda_handler(event, context):

    p = os.getenv("RKS_PRIVATE_KEY_PATH")
    VIENNA_TZ = ZoneInfo("Europe/Vienna")

    # ---- Weekly adherence (Wednesdays, Vienna time) ----
    if (datetime.now(VIENNA_TZ).weekday() == 1):  # Mon=0 ... Wed=2
        try:
            token = get_service_access_token()

            recent_participants = build_recent_participants(BASE_URL, RKS_PROJECT_ID, token)
            recent_ids = recent_participants["participantIdentifier"].dropna().tolist()
            print(f"[INFO] Computing adherence for {len(recent_ids)} participants")

            adherence = meal_day_counts(
                BASE_URL, RKS_PROJECT_ID, token, recent_ids,
                meals=MEALS, window_days=WINDOW_DAYS, snack=SNACK,
            )
            adherence_final = build_adherence_final(adherence, recent_participants)

            print("\n=== WEEKLY ADHERENCE ===")
            print(adherence_final.to_string(index=False))

            write_adherence_to_ddb(adherence_final)

        except Exception as e:
            # don't let adherence failures block the daily UH export
            print(f"[ERROR] Weekly adherence failed: {type(e).__name__}: {e}")
    else:
        print(f"[DEBUG] Skipping adherence:"
              f"weekday={datetime.now(VIENNA_TZ).weekday()}")

    # # Export UH data (yesterday)
    # participants = get_participants_from_ddb()
    # out = export_last_day_per_participant(participants, end_day_delta=1)
    # print("[DEBUG] About to process files:", out["files"])

    # # MINIMAL CHANGE: Skip S3 when DEBUG
    # if DEBUG:
    #     print("[DEBUG] DEBUG=true → skipping S3 upload")
    #     uploaded = []
    # else:
    #     uploaded = upload_to_s3(out["files"])

    # out["uploaded"] = uploaded
    # out["n_participants"] = len(participants)

    # return out
    return 0


# -------------------------
# Local main
# -------------------------
if __name__ == "__main__":
    # Assumes AWS profile/region creds are set (for DynamoDB and S3)
    res = lambda_handler({}, None)
    print(json.dumps(res, indent=2))

# if __name__ == "__main__":
#     participants = get_participants_from_ddb()
#     # res = backfill_last_7_days_per_participant(participants, end_day_delta=1)
#     # print(json.dumps(res, indent=2))
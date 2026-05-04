import os, csv, json, requests, boto3
import pandas as pd
from dateutil import parser
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo
from datetime import datetime, timezone, timedelta
from api_utils import *


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

# -------------------------
# DynamoDB: get participants
# -------------------------
dynamodb = boto3.resource("dynamodb", region_name=DDB_REGION) if DDB_REGION else boto3.resource("dynamodb")
table = dynamodb.Table(DDB_TABLE)


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


def get_snack_completion(base_url, project_id, access_token, first_meal, eligible_participants=None):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    ## DEBUG
    print(eligible_participants)

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
        batch = data.get("surveyAnswers", [])
        # Filter to eligible participants immediately if allowlist provided
        if eligible_participants is not None:
            batch = [a for a in batch if a.get("participantIdentifier") in eligible_participants]
        all_answers.extend(batch)
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

    # Keep only snacks within 28 days after first meal
    merged = merged[
        merged["date"] <= (merged["first_meal_date"] + pd.to_timedelta(27, unit="d"))
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


def check_tracking_t3(base_url, project_id, access_token, bucket):
    """
    Only computes meal adherence for participants who completed the t3-followup
    task within the last 2 weeks. Adherence window is 28 days from first meal.
    """
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    # ---- STEP 1: Find participants who completed t3-followup in last 2 weeks ----
    cutoff_2w = datetime.now(timezone.utc) - timedelta(weeks=2)

    url = f"{base_url}/api/v1/administration/projects/{project_id}/surveytasks"
    params = {"limit": 200, "surveyName": "t3-followup"}

    all_t3, page_id = [], None
    while True:
        if page_id:
            params["pageID"] = page_id
        r = requests.get(url, headers=headers, params=params)
        if r.status_code != 200:
            print(f"Failed t3 fetch: {r.status_code}, {r.text[:200]}")
            break
        data = r.json()
        all_t3.extend(data.get("surveyTasks", []))
        page_id = data.get("nextPageID")
        if not page_id:
            break

    print(f"[INFO] Retrieved {len(all_t3)} t3-followup tasks")

    if not all_t3:
        return pd.DataFrame()

    def safe_parse_dt(x):
        try:
            dt = parser.parse(x)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    df_t3 = pd.DataFrame(all_t3)
    df_t3["insertedDate_dt"] = df_t3["insertedDate"].apply(safe_parse_dt)

    eligible_participants = set(
        df_t3[
            (df_t3["status"] == "complete") &
            (df_t3["insertedDate_dt"] >= cutoff_2w)
        ]["participantIdentifier"].tolist()
    )

    print(f"[INFO] {len(eligible_participants)} participants completed t3-followup in last 2 weeks")

    if not eligible_participants:
        return pd.DataFrame()

    # ---- STEP 2: Fetch meal tasks for eligible participants only ----
    meal_names = {"log_breakfast_de", "log_lunch_de", "log_dinner_de"}
    params = {"limit": 200, "surveyName": "log_breakfast_de,log_lunch_de,log_dinner_de,log_snack_de"}

    all_tasks, page_id = [], None
    while True:
        if page_id:
            params["pageID"] = page_id
        r = requests.get(url, headers=headers, params=params)
        if r.status_code != 200:
            print(f"Failed meal fetch: {r.status_code}, {r.text[:200]}")
            break
        data = r.json()
        batch = data.get("surveyTasks", [])
        # Filter to eligible participants immediately to keep memory lean
        all_tasks.extend([t for t in batch if t.get("participantIdentifier") in eligible_participants])
        page_id = data.get("nextPageID")
        if not page_id:
            break

    print(f"[INFO] Retrieved {len(all_tasks)} meal tasks for eligible participants")

    if not all_tasks:
        return pd.DataFrame()

    # ---- STEP 3: Adherence logic, window = 28 days ----
    df = pd.DataFrame(all_tasks)[["participantIdentifier", "surveyName", "status", "insertedDate"]]
    print(f"Task status options: {df['status'].unique()}")

    def safe_parse_date(x):
        try:
            return parser.parse(x).date()
        except Exception:
            return None

    df["date"] = df["insertedDate"].apply(safe_parse_date)

    # First meal date per participant
    df_meals_initial = df[df["surveyName"].isin(meal_names)].dropna(subset=["date"])
    first_meal = (
        df_meals_initial.groupby("participantIdentifier", as_index=False)["date"]
        .min()
        .rename(columns={"date": "first_meal_date"})
    )

    # Limit to 28 days from first meal (days 0–27)
    df = pd.merge(df, first_meal, on="participantIdentifier", how="left")
    df["date"] = pd.to_datetime(df["date"])
    df["first_meal_date"] = pd.to_datetime(df["first_meal_date"])
    df["date_diff"] = (df["date"] - df["first_meal_date"]).dt.days
    df = df[df["date_diff"].between(0, 27)]

    # Daily status counts
    df_status_daily = (
        df[df["status"].isin(["complete", "incomplete", "closed"])]
        .assign(status=lambda x: x["status"].replace({"closed": "incomplete"}))
        .groupby(["participantIdentifier", "date", "status"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={"complete": "complete_meals", "incomplete": "incomplete_meals"})
    )
    for col in ["complete_meals", "incomplete_meals"]:
        if col not in df_status_daily.columns:
            df_status_daily[col] = 0

    # Snacks — pass eligible_participants allowlist to avoid scanning everyone
    df_snacks_daily = get_snack_completion(
        base_url, project_id, access_token, first_meal,
        eligible_participants=eligible_participants,
    )

    df_status_daily["date"] = pd.to_datetime(df_status_daily["date"]).dt.date
    df_snacks_daily["date"] = pd.to_datetime(df_snacks_daily["date"]).dt.date

    df_final = (
        df_status_daily
        .merge(df_snacks_daily, on=["participantIdentifier", "date"], how="outer")
        .fillna(0)
    )

    df_final["meals_total_day"] = df_final["complete_meals"] + df_final["incomplete_meals"]
    df_final["has_2plus_complete_meals"] = df_final["complete_meals"] >= 2
    df_final["has_2plus_any"] = (df_final["complete_meals"] + df_final["snacks_per_day"]) >= 2

    df_summary = (
        df_final.groupby("participantIdentifier")
        .agg(
            meals_completed=("complete_meals", "sum"),
            meals_total=("meals_total_day", "sum"),
            nb_days=("date", "nunique"),
            nb_days_2plus_meals=("has_2plus_complete_meals", "sum"),
            nb_days_2plus_any=("has_2plus_any", "sum"),
        )
        .reset_index()
    )

    df_summary["percentage_meals_tracked"] = (
        df_summary["meals_completed"] / df_summary["meals_total"] * 100
    ).round(1)

    df_summary["pct_days_2plus_meals"] = (
        df_summary["nb_days_2plus_meals"] / (df_summary["nb_days"] - 1) * 100
    ).round(1)

    df_summary["pct_days_2plus_any"] = (
        df_summary["nb_days_2plus_any"] / (df_summary["nb_days"] - 1) * 100
    ).round(1)

    return df_summary


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


def send_adherence_email(df_out):
    """
    Sends one summary email listing (sorted) participant email + nb_days_2plus_any (+ % of 28 days).
    Expects df_out to contain columns: email, nb_days_2plus_any
    """
    recipient_1 = os.environ["EMAIL_RECIPIENT_1"].strip()
    recipient_2 = os.environ["EMAIL_RECIPIENT_2"].strip()
    sender = os.environ["EMAIL_SENDER"].strip()

    subject = "MDH adherence summary (t3-followup completers, last 2 weeks)"

    # keep only rows with an email
    d = df_out.copy()
    d = d[d["email"].notna() & (d["email"].astype(str).str.strip() != "")]
    d["email"] = d["email"].astype(str).str.strip()

    # ensure numeric
    d["nb_days_2plus_any"] = pd.to_numeric(d["nb_days_2plus_any"], errors="coerce").fillna(0).astype(int)

    # sort by email
    d = d.sort_values("email")

    lines = []
    for _, row in d.iterrows():
        email = row["email"]
        n_days = int(row["nb_days_2plus_any"])
        pct = round((n_days / 28) * 100, 1)
        lines.append(
            f"{email}: Completed logging on {n_days} days, amounting to {pct}%, assuming total duration of 28 days."
        )

    if not lines:
        body_text = "No t3-followup completers with an email found."
    else:
        body_text = "\n".join(lines)

    ses.send_email(
        Source=sender,
        Destination={"ToAddresses": [recipient_1, recipient_2]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body_text}},
        },
    )


def attach_mdh_emails_and_print(df_adherence, base_url, project_id, access_token):
    participant_ids = df_adherence["participantIdentifier"].astype(str).unique().tolist()

    df_email = mdh_build_participant_email_df(
        base_url, project_id, access_token, participant_ids
    )

    # Only keep those with a valid enrollmentDate (passed filter)
    df_email_kept = df_email.dropna(subset=["enrollmentDate"])

    df_out = (
        df_adherence
        .merge(df_email_kept[["participantIdentifier", "email", "enrollmentDate"]],
               on="participantIdentifier", how="inner")
        .sort_values("participantIdentifier")
    )

    pd.set_option("display.max_rows", 500)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 220)

    print("\n=== ADHERENCE (t3-followup COMPLETERS, LAST 2 WEEKS) ===")
    print(df_out.to_string(index=False))

    print(
        f"\n[INFO] Participants after t3-followup filter: {len(df_out)} "
        f"(out of {df_adherence['participantIdentifier'].nunique()})"
    )

    return df_out


# def backfill_last_7_days_per_participant(participants, end_day_delta: int = 1, upload: bool = True):
#     """
#     One-time backfill: creates ONE JSON per participant per day for the last 7 days,
#     and (optionally) uploads each file to S3 using your existing upload_to_s3().

#     Days covered: [today - end_day_delta - 6, ..., today - end_day_delta] in Europe/Vienna.
#     Writes to: OUTPUT_DIR/<ID>/<YYYY-MM-DD>__id_<ID>.json
#     """
#     if str(OUTPUT_DIR).endswith(".csv"):
#         raise ValueError(f"OUTPUT_DIR must be a directory, got: {OUTPUT_DIR}")

#     os.makedirs(OUTPUT_DIR, exist_ok=True)

#     try:
#         tz = ZoneInfo("Europe/Vienna")
#         end_date = datetime.now(tz).date() - timedelta(days=end_day_delta)
#     except Exception:
#         end_date = datetime.now(timezone.utc).date() - timedelta(days=end_day_delta)

#     start_date = end_date - timedelta(days=6)

#     created_files = []

#     for p in participants:
#         pid = str(p.get("id") or "NOID").strip()
#         email = p["email"]

#         safe_id = pid.replace("/", "_").replace("\\", "_").replace(" ", "_")
#         participant_dir = os.path.join(OUTPUT_DIR, safe_id)
#         os.makedirs(participant_dir, exist_ok=True)

#         for i in range(7):
#             day = start_date + timedelta(days=i)
#             ymd = day.isoformat()

#             error = None
#             resp = None
#             try:
#                 resp = fetch_metrics_for_date(email, day)
#             except Exception as e:
#                 error = str(e)

#             filename = f"{ymd}__id_{safe_id}.json"
#             filepath = os.path.join(participant_dir, filename)

#             out_obj = {
#                 "id": pid,
#                 "email": email,
#                 "date": ymd,
#                 "pulled_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
#                 "ultrahuman_response": resp,
#                 "error": error,
#             }

#             with open(filepath, "w", encoding="utf-8") as f:
#                 json.dump(out_obj, f, ensure_ascii=False)

#             created_files.append(filepath)

#     uploaded = []
#     if upload and created_files:
#         uploaded = upload_to_s3(created_files)

#     return {
#         "start_date": start_date.isoformat(),
#         "end_date": end_date.isoformat(),
#         "files": created_files,
#         "uploaded": uploaded,
#     }


# -------------------------
# Lambda entry point
# -------------------------
def lambda_handler(event, context):
    DEBUG = False

    p = os.getenv("RKS_PRIVATE_KEY_PATH")
    VIENNA_TZ = ZoneInfo("Europe/Vienna")

    if (not DEBUG): # and (datetime.now(VIENNA_TZ).weekday() == 2):  # Mon=0 ... Wed=2
        df_adherence = check_tracking_t3(
            BASE_URL,
            RKS_PROJECT_ID,
            get_service_access_token(),
            bucket=None,
        )

        df_adherence_with_email = attach_mdh_emails_and_print(
            df_adherence,
            BASE_URL,
            RKS_PROJECT_ID,
            get_service_access_token(),
        )

        # Send out adherence metrics to study team
        send_adherence_email(df_adherence_with_email)
    else:
        print(f"[DEBUG] Skipping adherence: DEBUG={DEBUG}, weekday={datetime.now(VIENNA_TZ).weekday()}")

    # Export UH data (yesterday)
    participants = get_participants_from_ddb()
    out = export_last_day_per_participant(participants, end_day_delta=1)
    print("[DEBUG] About to process files:", out["files"])

    # MINIMAL CHANGE: Skip S3 when DEBUG
    if DEBUG:
        print("[DEBUG] DEBUG=true → skipping S3 upload")
        uploaded = []
    else:
        uploaded = upload_to_s3(out["files"])

    out["uploaded"] = uploaded
    out["n_participants"] = len(participants)

    return out


# -------------------------
# Local main
# -------------------------
# if __name__ == "__main__":
#     # Assumes AWS profile/region creds are set (for DynamoDB and S3)
#     res = lambda_handler({}, None)
#     print(json.dumps(res, indent=2))

if __name__ == "__main__":
    participants = get_participants_from_ddb()
    # res = backfill_last_7_days_per_participant(participants, end_day_delta=1)
    # print(json.dumps(res, indent=2))
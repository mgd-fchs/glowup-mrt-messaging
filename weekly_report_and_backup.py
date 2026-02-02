import os, csv, json, requests, boto3
import pandas as pd
from dateutil import parser
from dateutil.relativedelta import relativedelta
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
S3_BUCKET = os.environ.get("S3_BUCKET", "glowup-mdh")  # e.g. "my-bucket"
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


def export_last_7_days_per_participant(participants, end_day_delta: int = 1):
    """
    Creates ONE CSV per participant containing the last 7 days ending at (today - end_day_delta).
    Writes to: OUTPUT_DIR/<ID>/<WEEK>__id_<ID>.csv
    """
    # OUTPUT_DIR must be a directory, not a file
    if str(OUTPUT_DIR).endswith(".csv"):
        raise ValueError(f"OUTPUT_DIR must be a directory, got: {OUTPUT_DIR}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    end_date = datetime.now(timezone.utc).date() - timedelta(days=end_day_delta)
    start_date = end_date - timedelta(days=6)
    wl = week_label(end_date)  # e.g. 2026-W05

    created_files = []

    for p in participants:
        pid = str(p.get("id") or "NOID").strip()
        email = p["email"]

        # folder name must be just the ID
        safe_id = pid.replace("/", "_").replace("\\", "_").replace(" ", "_")
        participant_dir = os.path.join(OUTPUT_DIR, safe_id)
        os.makedirs(participant_dir, exist_ok=True)

        rows = []
        errors = []

        for i in range(7):
            day = start_date + timedelta(days=i)
            date_str = day.strftime("%d/%m/%Y")

            try:
                resp = fetch_metrics_for_date(email, day)
                raw = extract_metric_data(resp)
                non_empty = filter_non_empty(raw)
                rows.extend(metrics_to_rows(pid, email, date_str, non_empty))
            except Exception as e:
                errors.append({"date": date_str, "error": str(e)})

        # filename should be week + id
        filename = f"{wl}__id_{safe_id}.csv"
        filepath = os.path.join(participant_dir, filename)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["id", "email", "date", "metric_type", "timestamp", "value"],
            )
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

        # optional error log alongside the CSV
        if errors:
            err_path = os.path.join(participant_dir, f"{wl}__id_{safe_id}__errors.json")
            with open(err_path, "w", encoding="utf-8") as ef:
                json.dump(
                    {
                        "id": pid,
                        "email": email,
                        "week": wl,
                        "start": str(start_date),
                        "end": str(end_date),
                        "errors": errors,
                    },
                    ef,
                    indent=2,
                )

        created_files.append(filepath)

    return {
        "week": wl,
        "start_date": str(start_date),
        "end_date": str(end_date),
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
            if participant_id.lower().endswith(".csv"):
                participant_id = participant_id[:-4]

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


def check_tracking(base_url, project_id, access_token, bucket):
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    url = f"{base_url}/api/v1/administration/projects/{project_id}/surveytasks"
    params = {"limit": 200, "surveyName": "log_breakfast_de,log_lunch_de,log_dinner_de,log_snack_de"}

    all_tasks, page_id = [], None
    while True:
        if page_id:
            params["pageID"] = page_id
        r = requests.get(url, headers=headers, params=params)
        if r.status_code != 200:
            print(f"Failed: {r.status_code}, {r.text[:200]}")
            break
        data = r.json()
        tasks = data.get("surveyTasks", [])
        all_tasks.extend(tasks)
        page_id = data.get("nextPageID")
        if not page_id:
            break

    print(f"[INFO] Retrieved {len(all_tasks)} total tasks")

    if not all_tasks:
        return pd.DataFrame(columns=[
            "participantIdentifier", "meal_completed", "meal_total", "meal_pct",
            "days_2_plus_incl_snacks", "days_total", "days_2_plus_incl_snacks_pct"
        ])

    # ---- INITIAL CLEANING ----
    print(pd.DataFrame(all_tasks).columns)
    df = pd.DataFrame(all_tasks)[["participantIdentifier", "surveyName", "status", "insertedDate"]]
    # print(df.head(30))
    print(f"Task status options: {df['status'].unique()}")

    def safe_parse_date(x):
        try:
            return parser.parse(x).date()
        except Exception:
            return None

    df["date"] = df["insertedDate"].apply(safe_parse_date)

    # ---- DETERMINE FIRST MEAL DATE ----
    meal_names = {"log_breakfast_de", "log_lunch_de", "log_dinner_de"}
    df_meals_initial = df[df["surveyName"].isin(meal_names)]
    df_meals_nonan = df_meals_initial.dropna(subset=["date"])

    first_meal = (
        df_meals_nonan.groupby("participantIdentifier", as_index=False)["date"]
        .min()
        .rename(columns={"date": "first_meal_date"})
    )

    # ---- LIMIT TO FIRST 30 DAYS PER PARTICIPANT ----
    df = pd.merge(df, first_meal, on="participantIdentifier", how="left")
    df["date"] = pd.to_datetime(df["date"])
    df["first_meal_date"] = pd.to_datetime(df["first_meal_date"])
    df["date_diff"] = (df["date"] - df["first_meal_date"]).dt.days
    df = df[df["date_diff"].between(0, 30)]  # inclusive: first + 29 = 30 days total

    # ---- DAILY STATUS COUNTS ----
    df_status_daily = (
        df[df["status"].isin(["complete", "incomplete", "closed"])]
        .assign(status=lambda x: x["status"].replace({"closed": "incomplete"}))
        .groupby(["participantIdentifier", "date", "status"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={
            "complete": "complete_meals",
            "incomplete": "incomplete_meals"
        })
    )

    # ensure both columns exist even if one status is missing
    for col in ["complete_meals", "incomplete_meals"]:
        if col not in df_status_daily.columns:
            df_status_daily[col] = 0

    # print(df_status_daily.head(30))

    # --- GET SNACK COUNTS PER DAY ----
    df_snacks_daily = get_snack_completion(base_url, project_id, access_token, first_meal)

    # ensure consistent datetime dtype and normalize (strip time)
    df_status_daily["date"] = pd.to_datetime(df_status_daily["date"]).dt.date
    df_snacks_daily["date"] = pd.to_datetime(df_snacks_daily["date"]).dt.date

    # merge on both participant and date
    df_final = (
        df_status_daily
        .merge(
            df_snacks_daily,
            on=["participantIdentifier", "date"],
            how="outer"
        )
        .fillna(0)
    )

    # ---- DAILY DERIVED COUNTS ----
    df_final["meals_total_day"] = df_final["complete_meals"] + df_final["incomplete_meals"]
    df_final["has_2plus_complete_meals"] = df_final["complete_meals"] >= 2
    df_final["has_2plus_any"] = (df_final["complete_meals"] + df_final["snacks_per_day"]) >= 2

    # ---- AGGREGATE PER PARTICIPANT ----
    df_summary = (
        df_final.groupby("participantIdentifier")
        .agg(
            meals_completed=("complete_meals", "sum"),
            meals_total=("meals_total_day", "sum"),
            nb_days=("date", "nunique"),
            nb_days_2plus_meals=("has_2plus_complete_meals", "sum"),
            nb_days_2plus_any=("has_2plus_any", "sum")
        )
        .reset_index()
    )

    # ---- DERIVED PERCENTAGES ----
    df_summary["percentage_meals_tracked"] = (
        df_summary["meals_completed"] / df_summary["meals_total"] * 100
    ).round(1)

    df_summary["pct_days_2plus_meals"] = (
        df_summary["nb_days_2plus_meals"] / (df_summary["nb_days"]-1) * 100
    ).round(1)

    df_summary["pct_days_2plus_any"] = (
        df_summary["nb_days_2plus_any"] / (df_summary["nb_days"]-1) * 100
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
            # keep a traceable row for debugging (optional)
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

    subject = "MDH adherence summary (last 3 months enrollments)"

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
        body_text = "No enrolled participants with an email found in df_out."
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

    print("\n=== ADHERENCE (MDH EMAILS, ENROLLED WITHIN LAST 3 MONTHS ONLY) ===")
    print(df_out.to_string(index=False))

    print(
        f"\n[INFO] Participants after last-3-months filter: {len(df_out)} "
        f"(out of {df_adherence['participantIdentifier'].nunique()})"
    )

    return df_out




# -------------------------
# Lambda entry point
# -------------------------
def lambda_handler(event, context):

    p = os.getenv("RKS_PRIVATE_KEY_PATH")

    df_adherence = check_tracking(
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
    
    # Upload UH data to S3
    participants = get_participants_from_ddb()
    out = export_last_7_days_per_participant(participants, end_day_delta=1)
    print("[DEBUG] About to upload files:", out["files"])

    uploaded = upload_to_s3(out["files"])
    out["uploaded"] = uploaded
    out["n_participants"] = len(participants)

    return out

# -------------------------
# Local main
# -------------------------
if __name__ == "__main__":
    # Assumes AWS profile/region creds are set (for DynamoDB and S3)
    res = lambda_handler({}, None)
    print(json.dumps(res, indent=2))


# def backfill_last_n_weeks(participants, n_weeks: int = 10, end_day_delta: int = 1):
#     """
#     One-time backfill: for each of the past n_weeks (including the current week of end_date),
#     fetch 7 days of data per participant, write ONE CSV per participant-week, and upload to:

#       s3://<bucket>/ultrahuman_database/<ID>/<YYYY-Www>__id_<ID>.csv

#     Skips uploading if that participant-week has zero rows (no real data).

#     NOTE: This calls your existing helpers:
#       - fetch_metrics_for_date(email, day)
#       - extract_metric_data(resp)
#       - filter_non_empty(raw)
#       - metrics_to_rows(pid, email, date_str, non_empty)
#       - week_label(date_obj)
#       - upload_to_s3([filepath])
#     """

#     if str(OUTPUT_DIR).endswith(".csv"):
#         raise ValueError(f"OUTPUT_DIR must be a directory, got: {OUTPUT_DIR}")
#     os.makedirs(OUTPUT_DIR, exist_ok=True)

#     created_files = []
#     uploaded = []

#     # Anchor date (avoid partial today by default)
#     anchor_end_date = datetime.date.today() - datetime.timedelta(days=end_day_delta)

#     for w in range(n_weeks):
#         # Week window: end_date goes back by 7*w days; each window is exactly 7 days
#         end_date = anchor_end_date - datetime.timedelta(days=7 * w)
#         start_date = end_date - datetime.timedelta(days=6)
#         wl = week_label(end_date)

#         for p in participants:
#             pid = str(p.get("id") or "NOID").strip()
#             email = p["email"]

#             safe_id = pid.replace("/", "_").replace("\\", "_").replace(" ", "_")
#             filename = f"{wl}__id_{safe_id}.csv"
#             filepath = os.path.join(OUTPUT_DIR, filename)

#             rows = []
#             errors = []

#             for i in range(7):
#                 day = start_date + datetime.timedelta(days=i)
#                 date_str = day.strftime("%d/%m/%Y")

#                 try:
#                     resp = fetch_metrics_for_date(email, day)
#                     raw = extract_metric_data(resp)
#                     non_empty = filter_non_empty(raw)
#                     rows.extend(metrics_to_rows(pid, email, date_str, non_empty))
#                 except Exception as e:
#                     errors.append({"date": date_str, "error": str(e)})

#             # Only write + upload if there is actual data
#             if not rows:
#                 continue

#             with open(filepath, "w", newline="", encoding="utf-8") as f:
#                 writer = csv.DictWriter(
#                     f,
#                     fieldnames=["id", "email", "date", "metric_type", "timestamp", "value"],
#                 )
#                 writer.writeheader()
#                 for r in rows:
#                     writer.writerow(r)

#             created_files.append(filepath)

#             # Upload immediately (keeps memory low)
#             up = upload_to_s3([filepath])
#             uploaded.extend(up)

#             # Optional: upload errors only if you want them (comment out if not)
#             if errors:
#                 err_name = f"{wl}__id_{safe_id}__errors.json"
#                 err_path = os.path.join(OUTPUT_DIR, err_name)
#                 with open(err_path, "w", encoding="utf-8") as ef:
#                     json.dump(
#                         {
#                             "id": pid,
#                             "email": email,
#                             "week": wl,
#                             "start": str(start_date),
#                             "end": str(end_date),
#                             "errors": errors,
#                         },
#                         ef,
#                         indent=2,
#                     )
#                 created_files.append(err_path)
#                 uploaded.extend(upload_to_s3([err_path]))

#     return {"n_weeks": n_weeks, "created_files": created_files, "uploaded": uploaded}


# # -------------------------
# # One-time call to backfill past weeks (NOT part of the normal Lambda path)
# # -------------------------
# if __name__ == "__main__":
#     participants = get_participants_from_ddb()
#     res = backfill_last_n_weeks(participants, n_weeks=10, end_day_delta=1)
#     print(json.dumps(res, indent=2))

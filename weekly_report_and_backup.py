import os
import csv
import json
import datetime
import requests
import boto3
from dotenv import load_dotenv

load_dotenv()
# -------------------------
# Config
# -------------------------
BASE_URL_UH = "https://partner.ultrahuman.com/api/v1/metrics"
API_TOKEN = os.environ["UH_API_TOKEN"].strip()

DDB_TABLE = os.environ.get("DDB_TABLE", "UH_emails")
DDB_REGION = os.environ.get("AWS_REGION")  # Lambda sets this; locally set it or rely on config

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./uh_weekly_output")  # local runs
# In Lambda you can set OUTPUT_DIR="/tmp/uh_weekly_output"

# Optionally upload to S3 (leave unset to disable)
S3_BUCKET = os.environ.get("S3_BUCKET")  # e.g. "my-bucket"
S3_PREFIX = os.environ.get("S3_PREFIX", "uh_weekly_exports/")  # folder prefix


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


import os
import csv
import json
import datetime

def export_last_7_days_per_participant(participants, end_day_delta: int = 1):
    """
    Creates ONE CSV per participant containing the last 7 days ending at (today - end_day_delta).
    Writes to: OUTPUT_DIR/<ID>/<WEEK>__id_<ID>.csv
    """
    # OUTPUT_DIR must be a directory, not a file
    if str(OUTPUT_DIR).endswith(".csv"):
        raise ValueError(f"OUTPUT_DIR must be a directory, got: {OUTPUT_DIR}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    end_date = datetime.date.today() - datetime.timedelta(days=end_day_delta)
    start_date = end_date - datetime.timedelta(days=6)
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
            day = start_date + datetime.timedelta(days=i)
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
    """
    Upload each local CSV to:
      s3://<bucket>/ultrahuman_database/<ID>/<WEEK>__id_<ID>.csv

    Assumes local files are named like: "{week}__id_{id}.csv"
    """
    if not S3_BUCKET:
        return []

    s3 = boto3.client("s3")
    uploaded = []

    for path in filepaths:
        filename = os.path.basename(path)

        # Parse ID from filename: "{week}__id_{ID}.csv"
        participant_id = "UNKNOWN"
        parts = filename.split("__")
        if len(parts) >= 2 and parts[1].startswith("id_"):
            participant_id = parts[1][3:]  # "1005.csv" here
            if participant_id.lower().endswith(".csv"):
                participant_id = participant_id[:-4]  # remove ".csv"

        # Put into S3 "folder" = ultrahuman_database/<ID>/
        key = f"ultrahuman_database/{participant_id}/{filename}"

        s3.upload_file(path, S3_BUCKET, key)
        uploaded.append({"bucket": S3_BUCKET, "key": key})

    return uploaded


# -------------------------
# Lambda entry point
# -------------------------
def lambda_handler(event, context):
    participants = get_participants_from_ddb()
    out = export_last_7_days_per_participant(participants, end_day_delta=1)

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

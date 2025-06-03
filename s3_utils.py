from datetime import datetime, timezone
import boto3
import json
from botocore.exceptions import ClientError

def get_month_folder():
    return datetime.now(timezone.utc).strftime("%Y_%m_notification_logs")

def get_dated_filename(base_name):  # e.g., "scheduled_log.json"
    date_prefix = datetime.now(timezone.utc).strftime("%Y_%m_%d")
    return f"{date_prefix}_{base_name}"

def load_log(bucket, base_filename, dated=True):
    folder = get_month_folder()
    filename = get_dated_filename(base_filename) if dated else base_filename
    key = f"{folder}/{filename}"

    s3 = boto3.client('s3')
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj['Body'].read())
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            print(f"No log found at {key}")
            return {}
        else:
            raise

def save_log(bucket, base_filename, log_data, dated=True):
    folder = get_month_folder()
    filename = get_dated_filename(base_filename) if dated else base_filename
    key = f"{folder}/{filename}"

    s3 = boto3.client('s3')
    s3.put_object(Body=json.dumps(log_data), Bucket=bucket, Key=key)


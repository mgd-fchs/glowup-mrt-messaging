import time
import yaml
from dotenv import load_dotenv

from utils.jitai_utils import *
from utils.api_utils import *
from notifications import *
from core.participant import Participant  # import your new class

def lambda_handler(event, context):
    with open("config.yaml", "r") as f:
        config = yaml.safe_load(f)

    load_dotenv()
    base_url = config["base_url"]
    segment_ids = config["segment_ids"]
    signal_config = config.get("signals", {})
    context_config = config.get("context_providers", {})
    decision_points_config = config.get("decision_points", {})

    with open("api_config.yaml", "r") as api_file:
        api_config = yaml.safe_load(api_file)
    CONTEXT_PROVIDERS = load_context_providers(context_config, shared_config=api_config)

    print("Running MRT loop...")
    access_token = get_service_access_token()
    all_active_participants = {}
    participant_context_data = {}

    now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)

    for platform, seg_id in segment_ids.items():
        segment_participants = get_participants_by_segment(project_id, access_token, seg_id)

        for raw_p in segment_participants:
            participant = Participant.from_api(raw_p, platform)

            if not any(is_available_for_decision(now_utc, raw_p, dp_cfg) for dp_cfg in decision_points_config.values()):
                continue

            all_active_participants[participant.id] = participant

            provider = CONTEXT_PROVIDERS.get(platform)
            if not provider:
                print(f"No provider module for platform {platform} — skipping")
                continue

            participant_signals = {}

            for signal_name, signal_info in signal_config.items():
                method_name = signal_info.get("method")
                aggregate_name = signal_info.get("aggregate")
                required = signal_info.get("required", False)

                try:
                    fetch_func = getattr(provider, method_name)
                    raw_data = fetch_func(access_token, participant.id)

                    value = (getattr(provider, aggregate_name)(raw_data, participant.timezone)
                             if aggregate_name else raw_data)
                    participant_signals[signal_name] = value
                except Exception as e:
                    print(f"[WARN] Failed to get signal '{signal_name}' for {participant.id} on {platform}: {e}")
                    if required:
                        participant_signals[signal_name] = None

            participant_context_data[participant.id] = {
                "platform": participant.platform,
                "custom_fields": participant.custom_fields,
                **participant_signals
            }

            sync_logic = config.get("sync_reminder_logic", {})
            participant_context_data[participant.id]["needs_sync_reminder"] = evaluate_sync_reminder(
                participant_signals, sync_logic
            )

            signal_summary = ", ".join(
                f"{k}: {v:.2f}" if isinstance(v, float)
                else f"{k}: {v}" if v is not None
                else f"{k}: NA"
                for k, v in participant_signals.items()
            )
            print(f"{platform} - {participant.id} | {signal_summary}")

    assignments = randomize(participant_context_data)
    for pid, group in assignments.items():
        print(f"{pid} assigned to group: {group}")

    check_and_increment_tracking(base_url, project_id, access_token, BUCKET)
    schedule_notifications(assignments, participant_context_data)
    schedule_sync_reminders(participant_context_data)
    return {"status": "completed"}

if __name__ == "__main__":
    while True:
        lambda_handler("fz", "cd")
        time.sleep(300)

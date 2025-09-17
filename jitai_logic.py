import time
from jitai_utils import *
from api_utils import *
from notifications import *
import apple_health as AppleHealth
import health_connect as HealthConnect
import google_fit as GoogleFit
import fitbit as Fitbit

def lambda_handler(event, context):
    print("Running MRT loop...")
    segment_ids = {
        "iOS": "d06bb52f-fecb-4625-94ee-26fddbbec8d6",
        "Android": "126ab0db-2207-47ac-afbc-f8925270c4e4",
        "Fitbit": "5e15de8b-11cc-43d0-89fd-f80e2a51b277"
    }
    access_token = get_service_access_token()
    active_participant_ids_by_platform = {}
    participant_context_data = {}
    all_active_participants = {}

    for platform, seg_id in segment_ids.items():
        segment_participants = get_participants_by_segment(project_id, access_token, seg_id)
        print(segment_participants)
        active_participants = get_active_meal_window_participants(segment_participants)
        for p in active_participants:
            all_active_participants[p["participantIdentifier"]] = p
        active_ids = [p["participantIdentifier"] for p in active_participants]
        active_participant_ids_by_platform[platform] = active_ids
        print(f"{platform} - Active participant IDs: {active_ids}")

    for platform, participant_ids in active_participant_ids_by_platform.items():
        for pid in participant_ids:
            daily_steps = {}
            sleep_data = []
            
            if platform == "iOS":
                steps_data = AppleHealth.get_steps(access_token, project_id, pid, base_url)
                sleep_data = AppleHealth.get_sleep(access_token, project_id, pid, base_url)
                daily_steps = AppleHealth.aggregate_steps_by_source(steps_data)
            # elif platform == "Android":
            #     steps_data = GoogleFit.get_steps(access_token, project_id, pid, base_url)
            #     sleep_data = GoogleFit.get_sleep(access_token, project_id, pid, base_url)
            #     daily_steps = GoogleFit.aggregate_steps_by_source(steps_data)
            elif platform == "Fitbit":
                steps_data = Fitbit.get_steps(access_token, project_id, pid, base_url)
                sleep_data = Fitbit.get_sleep(access_token, project_id, pid, base_url)
                daily_steps = Fitbit.aggregate_steps_by_source(steps_data)

            total_steps = max(daily_steps.values()) if daily_steps else None
            total_sleep_ms = sum([s.get("duration", 0) for s in sleep_data])
            total_sleep_hours = total_sleep_ms / (1000 * 60 * 60)

            p_obj = all_active_participants.get(pid)
            participant_context_data[pid] = {
                "platform": platform,
                "total_steps": total_steps,
                "total_sleep_hours": total_sleep_hours,
                "active_mealtimes": p_obj.get("active_mealtimes", []) if p_obj else [],
                "custom_fields": p_obj.get("customFields", {}) if p_obj else {},
                "demographics": p_obj.get("demographics", {}) if p_obj else {}
            }
            participant_context_data[pid]["needs_sync_reminder"] = (
                total_steps is None and total_sleep_hours == 0
            )
            print(f"{platform} - {pid} - Steps: {total_steps}, Sleep (h): {total_sleep_hours:.2f}")

    assignments = randomize(participant_context_data)
    for pid, group in assignments.items():
        mealtimes = participant_context_data[pid].get("active_mealtimes", [])
        print(f"{pid} assigned to group: {group} | Active mealtime(s): {', '.join(mealtimes) if mealtimes else 'None'}")

    print("----DEBUG-----")
    print(f"Project ID: {project_id}")
    print(f"Bucket: {BUCKET}")

    check_and_increment_tracking(base_url, project_id, access_token, BUCKET)
    schedule_notifications(assignments, participant_context_data)
    schedule_sync_reminders(participant_context_data)
    return {"status": "completed"}

if __name__ == "__main__":
    while True:
        lambda_handler("fz", "cd")
        time.sleep(300)
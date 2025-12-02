import time
from jitai_utils import *
from api_utils import *
from notifications import *

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
            p_obj = all_active_participants.get(pid)
            participant_context_data[pid] = {
                "platform": platform,
                "active_mealtimes": p_obj.get("active_mealtimes", []) if p_obj else [],
                "custom_fields": p_obj.get("customFields", {}) if p_obj else {},
                "demographics": p_obj.get("demographics", {}) if p_obj else {}
            }

            # get email 
            participant_email = participant_context_data[pid]['custom_fields'].get("Ultrahuman_email")
            print(f"Participant {pid} has email: {participant_email}")
            
            if not participant_email:
                continue
            # check last timestamp
            timestamp_status = get_last_timestamp_status(base_url_uh, api_key, participant_email)
            
            print(f"Participant {pid} has timestamp status: {timestamp_status}")
            is_inactive = timestamp_status[participant_email]['stale']
            
            participant_context_data[pid]["needs_sync_reminder"] = (
                is_inactive
            )
            print(f"Participant {pid} last updated UH at timestamp {timestamp_status[participant_email]['last_ts']}, {timestamp_status[participant_email]['hours_ago']} hours ago")

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
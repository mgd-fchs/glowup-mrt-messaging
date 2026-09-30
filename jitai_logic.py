import time
from jitai_utils import *
from api_utils import *
from notifications import *

def get_finished_ids(access_token):
    """participantIdentifiers of everyone who has completed T3."""
    t3 = get_survey_tasks(access_token, base_url, project_id,
                          surveyName="t3-followup", status="complete")
    return {t["participantIdentifier"] for t in t3 if t.get("participantIdentifier")}

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

    finished_ids = get_finished_ids(access_token)
    print(f"[INFO] {len(finished_ids)} participants have completed T3 — excluded")

    for platform, seg_id in segment_ids.items():
        segment_participants = get_participants_by_segment(project_id, access_token, seg_id)
        n_all = len(segment_participants)
        segment_participants = [p for p in segment_participants
                                if p.get("participantIdentifier") not in finished_ids]
        in_study_ids = [p.get("participantIdentifier") for p in segment_participants]
        print(f"{platform} - in segment: {n_all} | still in study (no T3): {len(in_study_ids)} -> {in_study_ids}")

        active_participants = get_active_meal_window_participants(segment_participants)
        for p in active_participants:
            all_active_participants[p["participantIdentifier"]] = p
        active_ids = [p["participantIdentifier"] for p in active_participants]
        active_participant_ids_by_platform[platform] = active_ids
        print(f"{platform} - in meal window now: {len(active_ids)} -> {active_ids}")
     
    for platform, participant_ids in active_participant_ids_by_platform.items():
        for pid in participant_ids:
            p_obj = all_active_participants.get(pid)
            participant_context_data[pid] = {
                "platform": platform,
                "active_mealtimes": p_obj.get("active_mealtimes", []) if p_obj else [],
                "custom_fields": p_obj.get("customFields", {}) if p_obj else {},
                "demographics": p_obj.get("demographics", {}) if p_obj else {}
            }

    assignments = randomize(participant_context_data)
    for pid, group in assignments.items():
        mealtimes = participant_context_data[pid].get("active_mealtimes", [])
        print(f"{pid} assigned to group: {group} | Active mealtime(s): {', '.join(mealtimes) if mealtimes else 'None'}")

    print("----DEBUG-----")
    print(f"Project ID: {project_id}")
    print(f"Bucket: {BUCKET}")

    #check_and_increment_tracking(base_url, project_id, access_token, BUCKET)
    schedule_notifications(assignments, participant_context_data, project_id, access_token)
    schedule_sync_reminders(participant_context_data)
    return {"status": "completed"}

if __name__ == "__main__":
    while True:
        lambda_handler("fz", "cd")
        time.sleep(500)

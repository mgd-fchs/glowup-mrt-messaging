from abc import ABC, abstractmethod

class ContextProvider(ABC):
    def __init__(self):
        self.config = {}
        self.base_url = None
        self.project_id = None
        self.namespace = None  # Optional default

    def setup(self, config: dict):
        self.config = config or {}
        self.base_url = self.config.get("base_url")
        self.project_id = self.config.get("project_id")
        
    @abstractmethod
    def get_steps(self, access_token, project_id, pid):
        pass

    @abstractmethod
    def get_sleep(self, access_token, project_id, pid):
        pass

    def request_data(self, access_token, project_id, pid, type_filter=None):
        from datetime import datetime, timedelta, timezone
        import requests

        observed_after = (datetime.utcnow() - timedelta(hours=24)).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

        params = {
            "namespace": self.namespace,
            "participantIdentifier": pid,
            "observedAfter": observed_after
        }
        if type_filter:
            params["type"] = type_filter

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }

        response = requests.get(
            f"{self.base_url}/api/v1/administration/projects/{project_id}/devicedatapoints",
            headers=headers, params=params
        )
        response.raise_for_status()
        return response.json().get("deviceDataPoints", [])

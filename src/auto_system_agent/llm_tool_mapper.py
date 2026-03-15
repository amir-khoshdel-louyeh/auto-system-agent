import json
import os
import re
from typing import Optional, Set
from urllib import error, request


class LLMToolMapper:
    """Optional LLM-backed mapper that resolves user text to a whitelisted tool."""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self._url = str(config.get("url") or os.getenv("AUTO_AGENT_LLM_URL", "")).strip()
        self._api_key = str(config.get("api_key") or os.getenv("AUTO_AGENT_LLM_API_KEY", "")).strip()
        self._model = str(config.get("model") or os.getenv("AUTO_AGENT_LLM_MODEL", "gpt-4o-mini")).strip()
        timeout_value = str(config.get("timeout") or os.getenv("AUTO_AGENT_LLM_TIMEOUT", "8")).strip()
        self._timeout = float(timeout_value) if timeout_value else 8.0

    def is_available(self) -> bool:
        return bool(self._url)

    def map_intent(self, user_text: str, allowed_actions: Set[str]) -> Optional[str]:
        if not self.is_available() or not user_text.strip():
            return None

        prompt = (
            "Classify whether the user message requests a concrete automation task. "
            "Respond as strict JSON only: {\"action\": \"<allowed_action_or_none>\"}. "
            "If the message is general conversation, advice, or Q&A, return action=none. "
            f"Allowed actions: {sorted(allowed_actions)} and none. "
            f"User instruction: {user_text}"
        )

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a strict classifier that returns only JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }

        raw_response = self._post_json(payload)
        if raw_response is None:
            return None

        action = self._extract_action(raw_response)
        if action == "none":
            return None
        if action in allowed_actions:
            return action
        return None

    def _post_json(self, payload: dict) -> Optional[dict]:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        req = request.Request(self._url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (error.URLError, error.HTTPError, json.JSONDecodeError, TimeoutError):
            return None

    def _extract_action(self, response_json: dict) -> Optional[str]:
        if "action" in response_json and isinstance(response_json["action"], str):
            return response_json["action"].strip()

        content = (
            response_json.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not isinstance(content, str) or not content.strip():
            return None

        content = content.strip()
        if content.startswith("{"):
            try:
                parsed = json.loads(content)
                action_value = parsed.get("action")
                if isinstance(action_value, str):
                    return action_value.strip()
            except json.JSONDecodeError:
                pass

        match = re.search(r'"action"\s*:\s*"([a-z_]+)"', content)
        if match:
            return match.group(1).strip()

        token = content.split()[0].strip().strip('"{}')
        return token or None

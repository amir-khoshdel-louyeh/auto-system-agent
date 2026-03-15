import json
import os
import re
from typing import Optional, Sequence, Set
from urllib import error, request


class LLMConversationAssistant:
    """Handles conversational replies and optional tool extraction."""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self._url = str(config.get("url") or os.getenv("AUTO_AGENT_LLM_URL", "")).strip()
        self._api_key = str(config.get("api_key") or os.getenv("AUTO_AGENT_LLM_API_KEY", "")).strip()
        self._model = str(config.get("model") or os.getenv("AUTO_AGENT_LLM_MODEL", "gpt-4o-mini")).strip()
        timeout_value = str(config.get("timeout") or os.getenv("AUTO_AGENT_LLM_TIMEOUT", "8")).strip()
        self._timeout = float(timeout_value) if timeout_value else 8.0

    def is_available(self) -> bool:
        return bool(self._url)

    def resolve(
        self,
        user_text: str,
        allowed_actions: Set[str],
        history: Sequence[dict[str, str]],
    ) -> Optional[dict]:
        if not self.is_available() or not user_text.strip():
            return None

        prompt = (
            "You are an assistant inside a desktop automation app. "
            "Return JSON only with one of two shapes:\n"
            "1) Chat response: {\"type\": \"chat\", \"response\": \"...\"}\n"
            "2) Tool call: {\"type\": \"tool\", \"action\": \"...\", \"target\": \"...\", "
            "\"destination\": \"...\"}\n"
            "Only use actions from this whitelist: "
            f"{sorted(allowed_actions)}. "
            "Use type=chat for general Q&A. Use type=tool when the user clearly requests a task."
        )

        messages = [{"role": "system", "content": prompt}]
        messages.extend(history[-6:])
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.2,
        }

        raw = self._post_json(payload)
        if raw is None:
            return None

        parsed = self._extract_json(raw)
        if not parsed:
            return None

        item_type = str(parsed.get("type", "")).strip().lower()
        if item_type == "chat":
            response = str(parsed.get("response", "")).strip()
            if response:
                return {"type": "chat", "response": response}
            return None

        if item_type == "tool":
            action = str(parsed.get("action", "")).strip()
            if action not in allowed_actions:
                return None

            target = str(parsed.get("target", "")).strip()
            destination = str(parsed.get("destination", "")).strip()
            return {
                "type": "tool",
                "action": action,
                "target": target,
                "destination": destination,
            }

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

    def _extract_json(self, response_json: dict) -> Optional[dict]:
        if isinstance(response_json, dict) and "type" in response_json:
            return response_json

        content = (
            response_json.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not isinstance(content, str):
            return None

        text = content.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return None

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None

        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

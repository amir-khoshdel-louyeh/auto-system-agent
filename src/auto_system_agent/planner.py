import json
import os
import re
from urllib import error, request

from auto_system_agent.models import PlannedTask
from auto_system_agent.task_schema import IntermediateTask


SUPPORTED_ACTIONS = {
    "install_app",
    "create_folder",
    "compress",
    "move_path",
    "delete_path",
    "list_files",
    "run_command",
    "help",
}

OLLAMA_DEFAULT_URL = "http://localhost:11434/v1/chat/completions"
OLLAMA_DEFAULT_MODEL = "llama3.1"


class Planner:
    """LLM-only planner that delegates all intent parsing to OLLAMA.

    No regex or hard-coded command matching remains. Every planning decision
    is requested from the configured OLLAMA model. Tools themselves stay
    local; the model only decides *what* to do.
    """

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self._url = (
            str(config.get("url") or os.getenv("AUTO_AGENT_OLLAMA_URL", "")).strip()
            or str(config.get("url") or os.getenv("AUTO_AGENT_LLM_URL", "")).strip()
            or os.getenv("AUTO_AGENT_DEFAULT_LLM_URL", "").strip()
            or OLLAMA_DEFAULT_URL
        )
        self._model = (
            str(config.get("model") or os.getenv("AUTO_AGENT_OLLAMA_MODEL", "")).strip()
            or str(config.get("model") or os.getenv("AUTO_AGENT_LLM_MODEL", "")).strip()
            or os.getenv("AUTO_AGENT_DEFAULT_LLM_MODEL", "").strip()
            or OLLAMA_DEFAULT_MODEL
        )
        timeout_raw = str(
            config.get("timeout")
            or os.getenv("AUTO_AGENT_OLLAMA_TIMEOUT", "")
            or os.getenv("AUTO_AGENT_LLM_TIMEOUT", "")
            or os.getenv("AUTO_AGENT_DEFAULT_LLM_TIMEOUT", "")
            or "30"
        ).strip()
        try:
            self._timeout = float(timeout_raw) if timeout_raw else 30.0
        except ValueError:
            self._timeout = 30.0
        api_key_raw = str(config.get("api_key") or os.getenv("AUTO_AGENT_OLLAMA_API_KEY", "") or os.getenv("AUTO_AGENT_LLM_API_KEY", "")).strip()
        self._api_key = api_key_raw

    def plan(self, user_input: str) -> PlannedTask:
        return self.plan_tasks(user_input)[0]

    def plan_tasks(self, user_input: str) -> list[PlannedTask]:
        text = user_input.strip()
        if not text:
            return [PlannedTask(action="unknown", target="", raw_input="")]

        llm_tasks = self._plan_via_ollama(text)
        if llm_tasks is not None:
            return llm_tasks

        # OLLAMA unavailable or returned no usable tasks -> unknown so
        # Agent can ask the model for a conversational answer.
        return [PlannedTask(action="unknown", target=text, raw_input=text)]

    # --- OLLAMA interaction ---

    def _plan_via_ollama(self, user_input: str) -> list[PlannedTask] | None:
        if not self._url:
            return None

        system_prompt = (
            "You are the planner for a desktop automation agent. "
            "Given the user's instruction, decide whether it requests a system task or is general conversation.\n"
            f"Allowed tool actions are: {sorted(SUPPORTED_ACTIONS)} and unknown.\n"
            "unknown means the message is not a system task and should be answered conversationally.\n"
            "Respond ONLY as strict JSON with this shape:\n"
            '{"tasks": [{"action": "<action>", "target": "<target>", "options": {"destination": "<dest>"}}]}\n'
            "Rules:\n"
            "- For install_app, target is app name (e.g. vlc).\n"
            "- For create_folder, target is folder path.\n"
            "- For compress, target is path to compress.\n"
            "- For move_path, target is source path and options.destination is destination path.\n"
            "- For delete_path, target is path to delete.\n"
            "- For list_files, target is directory path (use '.' for current dir).\n"
            "- For run_command, target is the exact shell command to run.\n"
            "- For help, target is empty.\n"
            "- If the instruction contains multiple steps, return multiple entries in tasks in order.\n"
            "- If it is general chat, return {\"tasks\": [{\"action\": \"unknown\", \"target\": \"\"}]}\n"
            "- Never add fields outside the schema. Keep target strings as the user wrote them (do not invent paths)."
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            "temperature": 0,
            "stream": False,
        }

        raw = self._post_json(payload)
        if raw is None:
            return None

        parsed = self._extract_json(raw)
        if parsed is None:
            return None

        tasks_raw = parsed.get("tasks")
        if not isinstance(tasks_raw, list):
            # also accept single task shape
            if "action" in parsed:
                tasks_raw = [parsed]
            else:
                return None

        tasks: list[PlannedTask] = []
        for item in tasks_raw:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", "")).strip()
            target = str(item.get("target", "") or "").strip()
            options = item.get("options") if isinstance(item.get("options"), dict) else {}
            # Normalize unknown handling
            if action not in SUPPORTED_ACTIONS and action != "unknown":
                action = "unknown"
            try:
                candidate = IntermediateTask(
                    action=action,  # type: ignore[arg-type]
                    target=target,
                    raw_input=user_input,
                    options=options or {},
                )
                tasks.append(candidate.to_planned_task())
            except ValueError:
                tasks.append(PlannedTask(action="unknown", target=target, raw_input=user_input, options=options or {}))

        if not tasks:
            return None

        # Attach sequencing metadata for multi-step
        if len(tasks) > 1:
            for index, task in enumerate(tasks):
                if task.action == "unknown":
                    continue
                task.options["depends_on_steps"] = [] if index == 0 else [index]
                task.options["rollback_hint"] = self._rollback_hint(task)

        return tasks

    def _rollback_hint(self, task: PlannedTask) -> str:
        if task.action == "create_folder" and task.target:
            return f"delete_path {task.target}"
        if task.action == "move_path" and task.target:
            destination = str(task.options.get("destination", "")).strip()
            if destination:
                return f"move_path {destination} -> {task.target}"
        if task.action == "install_app" and task.target:
            return f"manual uninstall may be required for {task.target}"
        if task.action == "compress" and task.target:
            return f"delete generated archive for {task.target}"
        return "no automatic rollback available"

    def _post_json(self, payload: dict) -> dict | None:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = request.Request(self._url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (error.URLError, error.HTTPError, json.JSONDecodeError, TimeoutError, OSError, Exception):
            # Try native Ollama /api/chat as fallback if the URL was the default OpenAI-compat
            # and the server speaks native protocol.
            if self._url.endswith("/v1/chat/completions"):
                native_url = self._url.replace("/v1/chat/completions", "/api/chat")
                native_payload = {
                    "model": self._model,
                    "messages": payload["messages"],
                    "stream": False,
                }
                native_body = json.dumps(native_payload).encode("utf-8")
                native_req = request.Request(native_url, data=native_body, headers=headers, method="POST")
                try:
                    with request.urlopen(native_req, timeout=self._timeout) as resp:
                        native_raw = json.loads(resp.read().decode("utf-8"))
                        # Normalize native Ollama shape to OpenAI shape
                        content = native_raw.get("message", {}).get("content", "")
                        if content:
                            return {"choices": [{"message": {"content": content}}]}
                        return native_raw
                except Exception:
                    return None
            return None

    def _extract_json(self, response_json: dict) -> dict | None:
        if isinstance(response_json, dict) and "tasks" in response_json:
            return response_json
        if isinstance(response_json, dict) and "action" in response_json:
            return response_json

        content = (
            response_json.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not isinstance(content, str):
            return None
        text = content.strip()
        if not text:
            return None
        # Strip code fences
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()
        # Extract first JSON object
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

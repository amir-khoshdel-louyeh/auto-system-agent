import re
from pathlib import Path

from auto_system_agent.models import PlannedTask
from auto_system_agent.task_schema import IntermediateTask


DIRECT_COMMAND_PREFIXES = {
    "ls",
    "pwd",
    "whoami",
    "echo",
    "cat",
    "date",
    "uname",
    "df",
    "du",
    "ps",
}

HELP_WORDS = {"help"}


INSTALL_PATTERNS = (
    re.compile(r"^(?:please\s+)?install(?:\s+app)?\s+(.+)$", re.IGNORECASE),
    re.compile(r"^can\s+you\s+install\s+(.+)$", re.IGNORECASE),
)

CREATE_FOLDER_PATTERNS = (
    re.compile(r"^(?:please\s+)?create\s+(?:folder|directory)\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?make\s+(?:folder|directory)\s+(.+)$", re.IGNORECASE),
)

COMPRESS_PATTERNS = (
    re.compile(r"^(?:please\s+)?compress\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?zip\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?archive\s+(.+)$", re.IGNORECASE),
)

LIST_FILES_PATTERNS = (
    re.compile(r"^(?:please\s+)?list\s+files(?:\s+in\s+(.+))?$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?show\s+files(?:\s+in\s+(.+))?$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?list\s+directory(?:\s+(.+))?$", re.IGNORECASE),
)

MOVE_PATTERNS = (
    re.compile(r"^(?:please\s+)?move\s+(.+?)\s+to\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?rename\s+(.+?)\s+to\s+(.+)$", re.IGNORECASE),
)

DELETE_PATTERNS = (
    re.compile(r"^(?:please\s+)?delete\s+(?:file|folder|directory)?\s*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?remove\s+(?:file|folder|directory)?\s*(.+)$", re.IGNORECASE),
)

RUN_PATTERNS = (
    re.compile(r"^(?:please\s+)?run\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:please\s+)?execute\s+(.+)$", re.IGNORECASE),
)

MULTI_STEP_SPLIT_PATTERN = re.compile(
    r"\s*(?:,?\s+and then\s+|,?\s+then\s+|;\s*|,\s+(?=(?:install|create|make|compress|zip|archive|move|rename|delete|remove|list|show|run|execute)\b))",
    re.IGNORECASE,
)

APP_ALIASES = {
    "chrome": "google chrome",
    "google-chrome": "google chrome",
    "code": "visual studio code",
    "vscode": "visual studio code",
    "vlc media player": "vlc",
}


def _extract_first_group(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.match(text)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].strip()
    return text


def _normalize_path_arg(value: str) -> str:
    text = _strip_wrapping_quotes(value)
    if not text:
        return text
    if text == ".":
        return "."
    return str(Path(text).expanduser())


def _normalize_install_arg(value: str) -> str:
    normalized = _strip_wrapping_quotes(value).lower().strip()
    return APP_ALIASES.get(normalized, normalized)


def _extract_two_groups(
    patterns: tuple[re.Pattern[str], ...],
    text: str,
) -> tuple[str, str] | None:
    for pattern in patterns:
        match = pattern.match(text)
        if match:
            source = match.group(1).strip()
            destination = match.group(2).strip()
            if source and destination:
                return source, destination
    return None


class Planner:
    """Converts raw user text into a normalized task."""

    def plan(self, user_input: str) -> PlannedTask:
        return self.plan_tasks(user_input)[0]

    def plan_tasks(self, user_input: str) -> list[PlannedTask]:
        text = user_input.strip()
        if not text:
            return [PlannedTask(action="unknown", target="", raw_input="")]

        segments = [segment.strip() for segment in MULTI_STEP_SPLIT_PATTERN.split(text) if segment.strip()]
        if len(segments) <= 1:
            return [self._plan_single(text)]

        tasks: list[PlannedTask] = []
        for segment in segments:
            tasks.append(self._plan_single(segment, raw_input=text))
        for index, task in enumerate(tasks):
            if task.action == "unknown":
                continue
            depends_on = [] if index == 0 else [index]
            task.options["depends_on_steps"] = depends_on
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

    def _plan_single(self, user_input: str, raw_input: str | None = None) -> PlannedTask:
        text = user_input.strip()
        lowered = text.lower()
        task_raw_input = raw_input or text

        if lowered in HELP_WORDS:
            return self._build_task(action="help", raw_input=task_raw_input)

        app_name = _extract_first_group(INSTALL_PATTERNS, text)
        if app_name:
            return self._build_task(action="install_app", target=_normalize_install_arg(app_name), raw_input=task_raw_input)

        folder_name = _extract_first_group(CREATE_FOLDER_PATTERNS, text)
        if folder_name:
            return self._build_task(action="create_folder", target=_normalize_path_arg(folder_name), raw_input=task_raw_input)

        compress_target = _extract_first_group(COMPRESS_PATTERNS, text)
        if compress_target:
            return self._build_task(action="compress", target=_normalize_path_arg(compress_target), raw_input=task_raw_input)

        move_values = _extract_two_groups(MOVE_PATTERNS, text)
        if move_values:
            source, destination = move_values
            return self._build_task(
                action="move_path",
                target=_normalize_path_arg(source),
                raw_input=task_raw_input,
                options={"destination": _normalize_path_arg(destination)},
            )

        delete_target = _extract_first_group(DELETE_PATTERNS, text)
        if delete_target:
            return self._build_task(action="delete_path", target=_normalize_path_arg(delete_target), raw_input=task_raw_input)

        for pattern in LIST_FILES_PATTERNS:
            match = pattern.match(text)
            if match:
                target = match.group(1).strip() if match.group(1) else "."
                return self._build_task(action="list_files", target=_normalize_path_arg(target), raw_input=task_raw_input)

        run_target = _extract_first_group(RUN_PATTERNS, text)
        if run_target:
            return self._build_task(action="run_command", target=_strip_wrapping_quotes(run_target), raw_input=task_raw_input)

        command_head = lowered.split(maxsplit=1)[0] if lowered else ""
        if command_head in DIRECT_COMMAND_PREFIXES:
            return self._build_task(action="run_command", target=text, raw_input=task_raw_input)

        return self._build_task(action="unknown", target=text, raw_input=task_raw_input)

    def _build_task(
        self,
        *,
        action: str,
        target: str = "",
        raw_input: str,
        options: dict | None = None,
    ) -> PlannedTask:
        try:
            candidate = IntermediateTask(
                action=action,  # type: ignore[arg-type]
                target=target,
                raw_input=raw_input,
                options=options or {},
            )
            return candidate.to_planned_task()
        except ValueError:
            return PlannedTask(action="unknown", target=target, raw_input=raw_input)

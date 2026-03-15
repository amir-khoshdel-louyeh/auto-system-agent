import re

from auto_system_agent.models import PlannedTask


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


def _extract_first_group(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.match(text)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


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
        text = user_input.strip()
        lowered = text.lower()

        if lowered in HELP_WORDS:
            return PlannedTask(action="help", raw_input=text)

        app_name = _extract_first_group(INSTALL_PATTERNS, text)
        if app_name:
            return PlannedTask(action="install_app", target=app_name, raw_input=text)

        folder_name = _extract_first_group(CREATE_FOLDER_PATTERNS, text)
        if folder_name:
            return PlannedTask(action="create_folder", target=folder_name, raw_input=text)

        compress_target = _extract_first_group(COMPRESS_PATTERNS, text)
        if compress_target:
            return PlannedTask(action="compress", target=compress_target, raw_input=text)

        move_values = _extract_two_groups(MOVE_PATTERNS, text)
        if move_values:
            source, destination = move_values
            return PlannedTask(
                action="move_path",
                target=source,
                raw_input=text,
                options={"destination": destination},
            )

        delete_target = _extract_first_group(DELETE_PATTERNS, text)
        if delete_target:
            return PlannedTask(action="delete_path", target=delete_target, raw_input=text)

        for pattern in LIST_FILES_PATTERNS:
            match = pattern.match(text)
            if match:
                target = match.group(1).strip() if match.group(1) else "."
                return PlannedTask(action="list_files", target=target, raw_input=text)

        run_target = _extract_first_group(RUN_PATTERNS, text)
        if run_target:
            return PlannedTask(action="run_command", target=run_target, raw_input=text)

        command_head = lowered.split(maxsplit=1)[0] if lowered else ""
        if command_head in DIRECT_COMMAND_PREFIXES:
            return PlannedTask(action="run_command", target=text, raw_input=text)

        return PlannedTask(action="unknown", target=text, raw_input=text)

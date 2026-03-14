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

HELP_WORDS = {"help", "hello", "hi", "hey"}


class Planner:
    """Converts raw user text into a normalized task."""

    def plan(self, user_input: str) -> PlannedTask:
        text = user_input.strip()
        lowered = text.lower()

        if lowered in HELP_WORDS:
            return PlannedTask(action="help", raw_input=text)

        if lowered.startswith("install "):
            return PlannedTask(action="install_app", target=text[8:].strip(), raw_input=text)

        if lowered.startswith("create folder "):
            return PlannedTask(action="create_folder", target=text[14:].strip(), raw_input=text)

        if lowered.startswith("compress "):
            return PlannedTask(action="compress", target=text[9:].strip(), raw_input=text)

        if lowered.startswith("list files"):
            match = re.search(r"in\s+(.+)$", text, re.IGNORECASE)
            target = match.group(1).strip() if match else "."
            return PlannedTask(action="list_files", target=target, raw_input=text)

        if lowered.startswith("run "):
            return PlannedTask(action="run_command", target=text[4:].strip(), raw_input=text)

        command_head = lowered.split(maxsplit=1)[0] if lowered else ""
        if command_head in DIRECT_COMMAND_PREFIXES:
            return PlannedTask(action="run_command", target=text, raw_input=text)

        return PlannedTask(action="unknown", target=text, raw_input=text)

import re

from auto_system_agent.models import PlannedTask


class Planner:
    """Converts raw user text into a normalized task."""

    def plan(self, user_input: str) -> PlannedTask:
        text = user_input.strip()
        lowered = text.lower()

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

        return PlannedTask(action="unknown", target=text, raw_input=text)

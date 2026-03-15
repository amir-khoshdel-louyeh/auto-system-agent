from auto_system_agent.models import PlannedTask


class ToolSelector:
    """Resolves task actions to tool keys."""

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

    def select(self, task: PlannedTask) -> str:
        if task.action in self.SUPPORTED_ACTIONS:
            return task.action
        return "unknown"

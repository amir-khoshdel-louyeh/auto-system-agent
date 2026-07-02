from auto_system_agent.platforms import os_utils as _resolver
from auto_system_agent.models import PlannedTask
from auto_system_agent.services.llm_tool_mapper import LLMToolMapper
from auto_system_agent.tools import install_tool


class ToolSelector:
    """Resolves task actions to tool keys.

    P2.3: the deterministic C3 resolver runs first for install commands
    (package-manager choice is never left to the LLM); the LLM mapper only
    handles intent entities for non-whitelisted actions.
    """

    SUPPORTED_ACTIONS = {
        "run_command",
        "help",
    }

    def __init__(
        self,
        llm_mapper: LLMToolMapper | None = None,
        system_config: dict | None = None,
    ) -> None:
        self._llm_mapper = llm_mapper or LLMToolMapper()
        self._system_config = system_config if isinstance(system_config, dict) else None

    def select(self, task: PlannedTask) -> str:
        # If the planner already produced a whitelisted action, trust it
        # only if the action was produced by the LLM (planner is LLM-only).
        # We still validate against the whitelist so that non-whitelisted
        # actions become unknown.
        if task.action in self.SUPPORTED_ACTIONS:
            if task.action == "run_command":
                self._normalize_install_command(task)
            return task.action

        # Otherwise ask OLLAMA to map the raw intent to a tool.
        llm_selected = self._llm_mapper.map_intent(task.raw_input, self.SUPPORTED_ACTIONS)
        if llm_selected in self.SUPPORTED_ACTIONS:
            return llm_selected

        return "unknown"

    def _normalize_install_command(self, task: PlannedTask) -> bool:
        """Rewrite install targets to the local provider chain head.

        Returns True when the task target was rewritten.
        """
        text = (task.target or task.raw_input or "").strip()
        if not text:
            return False
        package = _resolver.extract_install_package(text)
        if not package:
            return False
        try:
            snapshot = _resolver.snapshot_system(system_config=self._system_config)
        except Exception:
            return False
        library_package = None
        try:
            library_package = install_tool.resolve_os_package_name(text, snapshot.os_name)
        except Exception:
            library_package = None
        try:
            best = _resolver.best_install_command(
                library_package or package,
                os_name=snapshot.os_name,
                distro_id=snapshot.distro_id,
                available=snapshot.available,
            )
        except Exception:
            return False
        if not best or best == text:
            return False
        task.target = best
        return True

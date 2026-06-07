from dataclasses import replace
from typing import Callable

from auto_system_agent.event_logger import EventLogger
from auto_system_agent.evaluator import Evaluator
from auto_system_agent.llm_conversation_assistant import LLMConversationAssistant
from auto_system_agent.llm_tool_mapper import LLMToolMapper
from auto_system_agent.models import Evaluation
from auto_system_agent.models import ExecutionResult
from auto_system_agent.models import PlannedTask
from auto_system_agent.models import ReActStep
from auto_system_agent.models import StepStatus
from auto_system_agent.planner import Planner
from auto_system_agent.result_formatter import ResultFormatter
from auto_system_agent.safe_executor import SafeExecutor
from auto_system_agent.tool_selector import ToolSelector
CONFIRMATION_YES_WORDS = {"yes", "y", "confirm", "ok", "proceed"}
CONFIRMATION_NO_WORDS = {"no", "n", "cancel", "stop"}
HIGH_RISK_ACTIONS = {"run_command"}
ACTION_RISK_LEVELS = {
    "run_command": "high",
    "help": "low",
    "unknown": "low",
}


class AutoSystemAgent:
    """End-to-end orchestration. All decisions and answers come from OLLAMA."""

    def __init__(
        self,
        planner: Planner | None = None,
        selector: ToolSelector | None = None,
        executor: SafeExecutor | None = None,
        formatter: ResultFormatter | None = None,
        assistant: LLMConversationAssistant | None = None,
        event_logger: EventLogger | None = None,
        evaluator: Evaluator | None = None,
        llm_config: dict | None = None,
        confirm_high_risk: bool = True,
        system_config: dict | None = None,
        max_react_iters: int = 3,
    ) -> None:
        llm_mapper = LLMToolMapper(config=llm_config)
        # Planner is now LLM-only and needs the OLLAMA config + system context
        self._planner = planner or Planner(config=llm_config, system_config=system_config)
        self._system_config = system_config if isinstance(system_config, dict) else None
        self._selector = selector or ToolSelector(llm_mapper=llm_mapper)
        self._executor = executor or SafeExecutor()
        self._formatter = formatter or ResultFormatter()
        self._assistant = assistant or LLMConversationAssistant(config=llm_config)
        self._event_logger = event_logger or EventLogger()
        self._evaluator = evaluator or Evaluator(config=llm_config)
        self._max_react_iters = max(1, max_react_iters)
        self._history: list[dict[str, str]] = []
        self._context: dict[str, str] = {"last_app": "", "last_path": ""}
        self._pending_confirmation: dict | None = None
        self._confirm_high_risk = confirm_high_risk
        self._llm_config = llm_config or {}

    def process(
        self,
        user_input: str,
        progress_callback: Callable[[StepStatus], None] | None = None,
    ) -> str:
        pending_reply = self._handle_pending_confirmation(user_input, progress_callback)
        if pending_reply is not None:
            return pending_reply

        tasks = self._planner.plan_tasks(user_input)
        if not tasks:
            # No hard-coded reply: ask OLLAMA
            return self._resolve_via_ollama(user_input, tasks, reason="planner_returned_no_tasks")

        if len(tasks) > 1:
            if self._requires_confirmation_for_tasks(tasks):
                reply = self._queue_confirmation(user_input, tasks, source_mode="multi_step")
                self._remember(user_input, reply)
                self._log_event(user_input=user_input, mode="confirmation_requested", planned_tasks=tasks, steps=[], reply=reply)
                return reply

            reply, steps = self._process_multi_step(user_input, tasks, progress_callback)
            # If multi-step failed, ask LLM to explain the last failure in chat
            if steps and not steps[-1]["result"]["success"]:
                last_idx = len(steps) - 1
                last_task = tasks[last_idx] if last_idx < len(tasks) else tasks[-1]
                last_tool = steps[-1]["tool"]
                last_msg = steps[-1]["result"]["message"]
                fake_result = ExecutionResult(success=False, message=last_msg)
                llm_explain = self._explain_failure_with_llm(user_input, last_tool, last_task, fake_result)
                if llm_explain:
                    combined = f"{reply}\n\n{llm_explain}"
                    self._remember(user_input, combined)
                    self._log_event(user_input=user_input, mode="multi_step_explained", planned_tasks=tasks, steps=steps, reply=combined)
                    return combined
            self._remember(user_input, reply)
            self._log_event(user_input=user_input, mode="multi_step", planned_tasks=tasks, steps=steps, reply=reply)
            return reply

        task = tasks[0]

        tool_key = self._selector.select(task)

        if tool_key != "unknown":
            if self._requires_confirmation_for_tasks([task]):
                reply = self._queue_confirmation(user_input, [task], source_mode="llm_planned")
                self._remember(user_input, reply)
                self._log_event(user_input=user_input, mode="confirmation_requested", planned_tasks=[task], steps=[], reply=reply)
                return reply

            reply, steps = self._run_react_loop(user_input, [task], progress_callback)
            if steps and not steps[-1]["result"]["success"]:
                last_tool = steps[-1]["tool"]
                last_msg = steps[-1]["result"]["message"]
                fake_result = ExecutionResult(success=False, message=last_msg)
                llm_explain = self._explain_failure_with_llm(user_input, last_tool, task, fake_result)
                if llm_explain:
                    combined = f"{reply}\n\n{llm_explain}"
                    self._remember(user_input, combined)
                    self._log_event(
                        user_input=user_input,
                        mode="ollama_tool_explained",
                        planned_tasks=tasks,
                        steps=steps,
                        reply=combined,
                    )
                    return combined
            self._remember(user_input, reply)
            self._log_event(
                user_input=user_input,
                mode="ollama_tool",
                planned_tasks=tasks,
                steps=steps,
                reply=reply,
            )
            return reply

        # Planner returned unknown -> ask OLLAMA for chat or tool
        return self._resolve_via_ollama(user_input, tasks, reason="planner_unknown")

    def _resolve_via_ollama(self, user_input: str, planned_tasks: list[PlannedTask], reason: str) -> str:
        allowed_actions = set(self._selector.SUPPORTED_ACTIONS)
        llm_result = self._assistant.resolve(user_input, allowed_actions, self._history)

        if llm_result and llm_result.get("type") == "chat":
            reply = llm_result["response"]
            self._update_context_from_chat(reply)
            self._remember(user_input, reply)
            self._log_event(user_input=user_input, mode="ollama_chat", planned_tasks=planned_tasks, steps=[], reply=reply)
            return reply

        if llm_result and llm_result.get("type") == "tool":
            llm_task = PlannedTask(
                action=llm_result["action"],
                target=llm_result.get("target", "") or "",
                raw_input=user_input,
                options={"destination": llm_result.get("destination", "")},
            )
            if self._requires_confirmation_for_tasks([llm_task]):
                reply = self._queue_confirmation(user_input, [llm_task], source_mode="ollama_tool")
                self._remember(user_input, reply)
                self._log_event(user_input=user_input, mode="confirmation_requested", planned_tasks=[llm_task], steps=[], reply=reply)
                return reply

            llm_tool_key = self._selector.select(llm_task)
            # No hard-coded fallback message: if OLLAMA suggests unknown tool, report OLLAMA error
            if llm_tool_key == "unknown":
                reply = self._ollama_unavailable_message()
                self._log_unresolved_intent(user_input=user_input, reason="ollama_mapped_unknown", planned_tasks=planned_tasks)
                self._remember(user_input, reply)
                self._log_event(user_input=user_input, mode="ollama_unmapped", planned_tasks=planned_tasks, steps=[], reply=reply)
                return reply

            result = self._executor.execute(llm_tool_key, llm_task)
            self._update_context_from_task(llm_task, result)
            if not result.success:
                llm_explain = self._explain_failure_with_llm(user_input, llm_tool_key, llm_task, result)
                if llm_explain:
                    self._remember(user_input, llm_explain)
                    self._log_event(
                        user_input=user_input,
                        mode="ollama_tool_explained",
                        planned_tasks=planned_tasks,
                        steps=[self._step_payload(llm_tool_key, llm_task, result)],
                        reply=llm_explain,
                    )
                    return llm_explain
            reply = self._formatter.format(result)
            self._remember(user_input, reply)
            self._log_event(
                user_input=user_input,
                mode="ollama_tool",
                planned_tasks=planned_tasks,
                steps=[self._step_payload(llm_tool_key, llm_task, result)],
                reply=reply,
            )
            return reply

        # OLLAMA unavailable or returned nothing: do not use hard-coded help string
        self._log_unresolved_intent(
            user_input=user_input,
            reason=reason,
            planned_tasks=planned_tasks,
        )
        reply = self._ollama_unavailable_message()
        self._remember(user_input, reply)
        self._log_event(user_input=user_input, mode="ollama_unavailable", planned_tasks=planned_tasks, steps=[], reply=reply)
        return reply

    def _ollama_unavailable_message(self) -> str:
        # No hard-coded canned answer: delegate to OLLAMA config hint so user can connect
        url = self._llm_config.get("url") if isinstance(self._llm_config, dict) else ""
        model = self._llm_config.get("model") if isinstance(self._llm_config, dict) else ""
        url_hint = url or "http://localhost:11434/v1/chat/completions"
        model_hint = model or "llama3.1"
        return (
            "OLLAMA is not reachable. All answers are generated by the model, so I cannot respond without it. "
            f"Please ensure OLLAMA is running at {url_hint} and the model '{model_hint}' is available "
            f"(e.g. `ollama pull {model_hint}` and `ollama serve`). Then configure the URL/model in Settings."
        )

    def _explain_failure_with_llm(self, user_input: str, tool_key: str, task: PlannedTask, result: ExecutionResult) -> str | None:
        """Ask LLM to explain a failed command without hard-coded messages."""
        if result.success:
            return None
        # Build a prompt that lets the model decide explanation, fix or next command
        try:
            history = list(self._history[-6:]) if self._history else []
            explain_prompt = (
                f"The user asked: '{user_input}'. "
                f"The agent executed '{task.target}' via {tool_key} and it failed with output: '{result.message}'. "
                "Explain in the user's language why it failed and suggest what to do next. "
                "If you suggest a corrected command, include it in the explanation. "
                "Do not hallucinate new flatpak IDs - if the ID was wrong, explain that it does not exist in flathub and suggest how to search (flatpak search / dnf search) instead of inventing another ID. "
                "Respond as a helpful assistant, not as JSON."
            )
            llm_reply = self._assistant.resolve(explain_prompt, set(self._selector.SUPPORTED_ACTIONS), history)
            if llm_reply and llm_reply.get("type") == "chat" and llm_reply.get("response"):
                return str(llm_reply["response"]).strip()
            # Fallback: if LLM returns plain text chat, _assistant already handles it
            if llm_reply and isinstance(llm_reply.get("response"), str):
                return str(llm_reply["response"]).strip()
        except Exception:
            pass
        return None

    def has_pending_confirmation(self) -> bool:
        return self._pending_confirmation is not None

    def get_pending_confirmation_summary(self) -> str:
        if not self._pending_confirmation:
            return ""

        tasks: list[PlannedTask] = self._pending_confirmation.get("tasks", [])
        if not tasks:
            return ""

        parts = []
        for task in tasks:
            parts.append(f"{task.action} {task.target or ''}".strip())
        return "; ".join(parts)

    def get_pending_confirmation_details(self) -> list[dict[str, str]]:
        if not self._pending_confirmation:
            return []

        tasks: list[PlannedTask] = self._pending_confirmation.get("tasks", [])
        details: list[dict[str, str]] = []
        for task in tasks:
            details.append(
                {
                    "action": task.action,
                    "target": task.target or "",
                    "risk_level": ACTION_RISK_LEVELS.get(task.action, "low"),
                    "preview": self._preview_for_task(task),
                }
            )
        return details

    def confirm_pending(self, progress_callback: Callable[[str], None] | None = None) -> str | None:
        return self._handle_pending_confirmation("yes", progress_callback)

    def cancel_pending(self) -> str | None:
        return self._handle_pending_confirmation("no", None)

    def _process_multi_step(
        self,
        user_input: str,
        tasks: list[PlannedTask],
        progress_callback: Callable[[StepStatus], None] | None = None,
    ) -> tuple[str, list[dict]]:
        return self._run_react_loop(user_input, tasks, progress_callback)

    def _run_react_loop(
        self,
        user_input: str,
        initial_tasks: list[PlannedTask],
        progress_callback: Callable[[StepStatus], None] | None = None,
    ) -> tuple[str, list[dict]]:
        """Thought -> Act -> Observe -> Evaluate loop over planned tasks."""
        tasks = list(initial_tasks)
        scratchpad: list[ReActStep] = []
        step_payloads: list[dict] = []

        for _ in range(self._max_react_iters):
            repaired: list[PlannedTask] | None = None
            aborted: Evaluation | None = None

            for task in tasks:
                tool_key = self._selector.select(task)
                step_no = len(scratchpad) + 1
                self._notify(progress_callback, StepStatus(step=step_no, total=step_no, tool=tool_key, state="running"))

                if tool_key == "unknown":
                    unknown_result = ExecutionResult(
                        success=False,
                        message=f"OLLAMA could not map step to a supported tool: {task.target or task.raw_input}",
                    )
                    evaluation = self._evaluator.evaluate_with_llm(
                        user_input, task, tool_key, unknown_result, scratchpad
                    )
                    scratchpad.append(ReActStep(thought=evaluation.reason, task=task, tool=tool_key, result=unknown_result))
                    step_payloads.append(self._step_payload(tool_key, task, unknown_result))
                    self._notify(
                        progress_callback,
                        StepStatus(step=step_no, total=step_no, tool=tool_key, state="failed", message=unknown_result.message),
                    )
                else:
                    if self._requires_confirmation_for_tasks([task]):
                        reply = self._queue_confirmation(user_input, [task], source_mode="react_loop")
                        self._remember(user_input, reply)
                        self._log_event(user_input=user_input, mode="confirmation_requested", planned_tasks=[task], steps=step_payloads, reply=reply)
                        pending_results = [step.result for step in scratchpad]
                        return (f"{self._formatter.format_many(pending_results)}\n\n{reply}".strip() if pending_results else reply), step_payloads

                    result = self._executor.execute(tool_key, task)
                    self._update_context_from_task(task, result)
                    evaluation = self._evaluator.evaluate_with_llm(user_input, task, tool_key, result, scratchpad)
                    scratchpad.append(ReActStep(thought=evaluation.reason, task=task, tool=tool_key, result=result))
                    step_payloads.append(self._step_payload(tool_key, task, result))
                    self._notify(
                        progress_callback,
                        StepStatus(
                            step=step_no,
                            total=step_no,
                            tool=tool_key,
                            state="done" if result.success and evaluation.verdict == "done" else "failed" if evaluation.verdict in ("abort",) or not result.success else "done",
                            message=evaluation.reason or result.message,
                        ),
                    )

                if evaluation.verdict == "done":
                    continue
                if evaluation.verdict == "abort":
                    aborted = evaluation
                    break
                # retry / replan: ask planner for corrected tasks and start next iteration
                repaired = self._repair_tasks(user_input, scratchpad, evaluation)
                break

            if aborted is not None:
                results = [step.result for step in scratchpad]
                return f"{self._formatter.format_many(results)}\n\nStopped: {aborted.reason}".strip(), step_payloads

            if repaired is None:
                # Either all steps done, or planner could not repair.
                results = [step.result for step in scratchpad]
                return self._formatter.format_many(results), step_payloads

            tasks = repaired

        results = [step.result for step in scratchpad]
        return f"{self._formatter.format_many(results)}\n\nStopped after {self._max_react_iters} attempts.".strip(), step_payloads

    def _repair_tasks(
        self,
        user_input: str,
        scratchpad: list[ReActStep],
        evaluation: Evaluation,
    ) -> list[PlannedTask] | None:
        repair = getattr(self._planner, "plan_repair", None)
        if not callable(repair):
            return None
        try:
            return repair(user_input, scratchpad, evaluation)
        except Exception:
            return None

    def _update_context_from_chat(self, reply: str) -> None:
        # Terminal mode: no app extraction needed
        return

    def _update_context_from_task(self, task: PlannedTask, result: ExecutionResult) -> None:
        if not result.success:
            return
        # For run_command, try to capture last path from command target
        if task.action == "run_command" and task.target:
            # Simple heuristic: last token that looks like a path
            import shlex

            try:
                parts = shlex.split(task.target)
                for token in reversed(parts):
                    if "/" in token or token.startswith("~") or token.endswith(".py") or token.endswith(".txt"):
                        self._context["last_path"] = token
                        break
                else:
                    if task.target.strip():
                        self._context["last_path"] = task.target.strip()
            except ValueError:
                self._context["last_path"] = task.target.strip()

    def _remember(self, user_text: str, assistant_text: str) -> None:
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": assistant_text})
        if len(self._history) > 20:
            self._history = self._history[-20:]

    def _notify(self, callback: Callable[[StepStatus], None] | None, status: StepStatus) -> None:
        if callback:
            callback(status)

    def _handle_pending_confirmation(
        self,
        user_input: str,
        progress_callback: Callable[[StepStatus], None] | None,
    ) -> str | None:
        if not self._pending_confirmation:
            return None

        decision = user_input.strip().lower()
        if decision in CONFIRMATION_NO_WORDS:
            pending = self._pending_confirmation
            self._pending_confirmation = None
            reply = "Cancelled pending action."
            self._remember(user_input, reply)
            self._log_event(
                user_input=user_input,
                mode="confirmation_cancelled",
                planned_tasks=pending["tasks"],
                steps=[],
                reply=reply,
            )
            return reply

        if decision not in CONFIRMATION_YES_WORDS:
            reply = "Confirmation required. Reply 'yes' to continue or 'no' to cancel."
            self._remember(user_input, reply)
            return reply

        pending = self._pending_confirmation
        self._pending_confirmation = None
        tasks: list[PlannedTask] = pending["tasks"]
        source_input: str = pending["source_input"]

        if len(tasks) > 1:
            reply, steps = self._process_multi_step(source_input, tasks, progress_callback)
            self._remember(user_input, reply)
            self._log_event(
                user_input=source_input,
                mode="confirmed_multi_step",
                planned_tasks=tasks,
                steps=steps,
                reply=reply,
            )
            return reply

        task = tasks[0]
        tool_key = self._selector.select(task)
        if tool_key == "unknown":
            reply = self._ollama_unavailable_message()
            self._remember(user_input, reply)
            return reply

        self._notify(progress_callback, StepStatus(step=1, total=1, tool=tool_key, state="running"))
        result = self._executor.execute(tool_key, task)
        self._notify(
            progress_callback,
            StepStatus(step=1, total=1, tool=tool_key, state="done" if result.success else "failed", message=result.message),
        )
        self._update_context_from_task(task, result)
        reply = self._formatter.format(result)
        self._remember(user_input, reply)
        self._log_event(
            user_input=source_input,
            mode="confirmed_single_step",
            planned_tasks=tasks,
            steps=[self._step_payload(tool_key, task, result)],
            reply=reply,
        )
        return reply

    def _queue_confirmation(self, user_input: str, tasks: list[PlannedTask], source_mode: str) -> str:
        self._pending_confirmation = {
            "source_input": user_input,
            "source_mode": source_mode,
            "tasks": tasks,
        }

        summary = "; ".join(
            f"{task.action} {task.target or ''}".strip() for task in tasks
        )
        return (
            "Confirmation required for high-risk action(s): "
            f"{summary}. Reply 'yes' to continue or 'no' to cancel."
        )

    def _preview_for_task(self, task: PlannedTask) -> str:
        # Terminal mode: preview is the shell command itself
        if task.action == "run_command":
            return task.target or task.raw_input
        return f"{task.action} {task.target or ''}".strip()

    def _requires_confirmation_for_tasks(self, tasks: list[PlannedTask]) -> bool:
        if not self._confirm_high_risk:
            return False
        # In terminal mode, only destructive commands need confirmation
        for task in tasks:
            if task.action not in HIGH_RISK_ACTIONS:
                continue
            if task.action == "run_command" and task.target:
                low = task.target.lower()
                # Only require confirmation for destructive patterns
                dangerous = ["rm ", "rm -", "sudo ", "mkfs", " dd ", "shutdown", "reboot", ":(){", "chmod 777", "> /dev/"]
                if any(pat in low for pat in dangerous):
                    return True
                # Safe commands like touch, mkdir, ls, cat, echo, pwd, cd do not need confirmation
                return False
            return True
        return False

    def _step_payload(self, tool_key: str, task: PlannedTask, result: ExecutionResult) -> dict:
        decision = str(result.data.get("policy_decision", "")).strip() or "approved"
        if "blocked" in result.message.lower():
            decision = "blocked"
        reason = str(result.data.get("policy_reason", "")).strip() or ("safety_policy" if decision == "blocked" else "allowed_action")
        return {
            "tool": tool_key,
            "task": {
                "action": task.action,
                "target": task.target or "",
                "options": dict(task.options),
            },
            "audit": {
                "decision": decision,
                "reason": reason,
                "risk_score": result.data.get("risk_score"),
                "risk_level": result.data.get("risk_level", ""),
            },
            "result": {
                "success": result.success,
                "message": result.message,
            },
        }

    def _log_event(
        self,
        *,
        user_input: str,
        mode: str,
        planned_tasks: list[PlannedTask],
        steps: list[dict],
        reply: str,
    ) -> None:
        self._event_logger.log(
            {
                "mode": mode,
                "user_input": user_input,
                "planned_tasks": [
                    {
                        "action": task.action,
                        "target": task.target or "",
                        "options": dict(task.options),
                    }
                    for task in planned_tasks
                ],
                "steps": steps,
                "reply": reply,
            }
        )

    def _log_unresolved_intent(self, *, user_input: str, reason: str, planned_tasks: list[PlannedTask]) -> None:
        self._event_logger.log(
            {
                "mode": "unresolved_intent",
                "reason": reason,
                "user_input": user_input,
                "planned_actions": [task.action for task in planned_tasks],
                "history_size": len(self._history),
            }
        )

from auto_system_agent.planner import Planner
from auto_system_agent.result_formatter import ResultFormatter
from auto_system_agent.safe_executor import SafeExecutor
from auto_system_agent.tool_selector import ToolSelector


def run_cli() -> None:
    planner = Planner()
    selector = ToolSelector()
    executor = SafeExecutor()
    formatter = ResultFormatter()

    print("Auto System Agent CLI")
    print("Type 'exit' to quit.")

    while True:
        user_input = input("You> ").strip()
        if user_input.lower() in {"exit", "quit"}:
            print("Agent> Bye")
            break

        task = planner.plan(user_input)
        tool_key = selector.select(task)
        result = executor.execute(tool_key, task)
        print(f"Agent> {formatter.format(result)}")

from auto_system_agent.agent import AutoSystemAgent


def run_cli() -> None:
    agent = AutoSystemAgent()

    print("Auto System Agent CLI")
    print("Type 'exit' to quit.")

    while True:
        user_input = input("You> ").strip()
        if user_input.lower() in {"exit", "quit"}:
            print("Agent> Bye")
            break

        print(f"Agent> {agent.process(user_input)}")

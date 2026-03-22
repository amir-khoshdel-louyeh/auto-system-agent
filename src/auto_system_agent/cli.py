from auto_system_agent.agent import AutoSystemAgent
from auto_system_agent.settings import SettingsStore


def run_cli() -> None:
    settings_store = SettingsStore()
    settings = settings_store.load()
    llm_config = settings_store.resolve_llm_config(settings)
    agent = AutoSystemAgent(llm_config=llm_config)

    print("Auto System Agent CLI")
    print("Type 'exit' to quit.")

    while True:
        user_input = input("You> ").strip()
        if user_input.lower() in {"exit", "quit"}:
            print("Agent> Bye")
            break

        print(f"Agent> {agent.process(user_input)}")

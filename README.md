# Auto System Agent

**Auto System Agent** is a simple cross-platform Agentic AI designed to automate operating system tasks using natural language commands.

The system allows users to type instructions such as installing applications, creating folders, running terminal commands, or managing files. An AI agent interprets these instructions and selects the appropriate system tools to execute the requested task.

The project is designed to work across **Windows, Linux, and macOS** by detecting the operating system and using the correct commands or package managers for each platform.

The goal of this project is to demonstrate how **agent-based AI systems can automate everyday system operations** in a practical and controlled way while maintaining safety through restricted command execution.

## Key Features

- Natural language command interface
- AI agent for task interpretation
- Modular tool-based architecture
- Cross-platform OS support
- File and folder management
- Application installation via system package managers
- Safe command execution

## Example Commands


install vlc
create folder test
open firefox
list files in downloads


## Goal of the Project

This project focuses on **simplicity and practical functionality**, providing a basic framework for building AI agents that interact with operating systems.

Future improvements may include a graphical interface, more tools, multi-step task planning, and expanded application libraries.

## LLM Token Setup

The app supports two modes so installers can ship a ready-to-use setup while advanced users can override with their own token.

### 1) Bundled mode (preconfigured)

Set these environment variables before launching the app:

- `AUTO_AGENT_DEFAULT_LLM_URL`
- `AUTO_AGENT_DEFAULT_LLM_API_KEY`
- `AUTO_AGENT_DEFAULT_LLM_MODEL` (optional, default is `gpt-4o-mini`)
- `AUTO_AGENT_DEFAULT_LLM_TIMEOUT` (optional, default is `8`)

Then open the GUI and keep **Settings -> LLM Settings -> Provider Mode = Bundled**.

### 2) Custom mode (user token)

Each user can configure their own provider values:

1. Open **Settings -> LLM Settings**.
2. Select **Provider Mode = Custom**.
3. Fill in URL, API key, model, and timeout.
4. Save.

The values are stored locally at `~/.auto_system_agent/settings.json` and are used by both GUI and CLI.

"""Auto System Agent — one command starts the API and the workbench.

    python main.py

The launcher (item [105]) reuses an API already answering on the port or
starts one, logs in with ``APP_ADMIN_USER``/``APP_ADMIN_PASSWORD`` (dev
defaults ``admin``/``admin``), and opens the four-tab workbench. Useful flags:
``--server-only``, ``--no-server``, ``--port N`` and ``--chat`` (the legacy
chat client).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from launcher import main

if __name__ == "__main__":
    raise SystemExit(main())

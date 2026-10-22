"""PyInstaller entry point for jobforge-agent.exe (console subsystem; see ARCHITECTURE 7.1)."""
import sys

from jobforge_agent.main import main

if __name__ == "__main__":
    sys.exit(main())

"""JobForge background agent. The only process that opens the JobForge database (SPEC D4)."""

__version__ = "0.3.7"
# Bumped when the IPC contract changes; the host refuses an agent with a different value.
PROTOCOL_VERSION = 1

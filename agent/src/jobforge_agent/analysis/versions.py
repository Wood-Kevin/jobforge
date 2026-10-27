"""Version labels that key posting_analyses (ARCHITECTURE 4.2). P1.4 queues analyses with
these; P1.6 implements the stages they name. Bump a label whenever its stage's behavior changes,
so new analyses are created instead of old ones being silently reinterpreted."""

EXTRACTION_PROMPT_VERSION = "e1-v2"  # 2026-10-27: subject "duty"; one item per bullet
SEGMENTER_VERSION = "seg-v1"
INTERPRETER_VERSION = "interp-v4"  # 2026-10-27: a "duty" is context unless code reads a condition or a qualification in it
# interp-v3  # 2026-10-24 (calibration E1-E2): availability type, zone/remote-region residency cues, "authorization to work"

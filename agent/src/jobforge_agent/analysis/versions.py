"""Version labels that key posting_analyses (ARCHITECTURE 4.2). P1.4 queues analyses with
these; P1.6 implements the stages they name. Bump a label whenever its stage's behavior changes,
so new analyses are created instead of old ones being silently reinterpreted."""

EXTRACTION_PROMPT_VERSION = "e1-v1"
SEGMENTER_VERSION = "seg-v1"
INTERPRETER_VERSION = "interp-v2"  # P1.7: residency cues for "local to" and on-site days per week

"""Version labels that key posting_analyses (ARCHITECTURE 4.2). P1.4 queues analyses with
these; P1.6 implements the stages they name. Bump a label whenever its stage's behavior changes,
so new analyses are created instead of old ones being silently reinterpreted."""

EXTRACTION_PROMPT_VERSION = "e1-v1"
SEGMENTER_VERSION = "seg-v2"  # 2026-10-27: "Position Responsibilities", "Duties and Responsibilities", "Required:", "Required Skills and Qualifications"
INTERPRETER_VERSION = "interp-v8"  # 2026-10-28: a LinkedIn profile asked of applicants is a hiring step too
# interp-v7 2026-10-27: a background check is a step of the hiring process, context
# interp-v6 2026-10-27: a duty done with named tools keeps them as a nice-to-have (interp-v5: split lines merged)
# interp-v4 2026-10-27: a line under a duties heading that asks nothing of the candidate and states no condition is context
# interp-v3 2026-10-24 (calibration E1-E2): availability type, zone/remote-region residency cues, "authorization to work"

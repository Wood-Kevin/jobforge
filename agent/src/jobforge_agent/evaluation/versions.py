"""Version labels that key evaluations and recommendations (SPEC D10). Bump MATCH_PROMPT_VERSION
when the E3 prompt, its schema or E4 validation changes (new evaluations); bump
VERDICT_RULE_VERSION with a new verdict_rules/vN.yaml when E5/E6 change (recompute only, no
model calls)."""

MATCH_PROMPT_VERSION = "m2-v2"  # 2026-10-26: years requirements cite every item that shows the experience (m2-v1, 2026-10-25: "transferable" narrowed to the same task with another tool or setting; multi-part requirements judged by their core)
VERDICT_RULE_VERSION = "v4"  # 2026-10-25: many unknowns make an unshown STRETCH (never SKIP); years tolerance 1 year / 25% (v3 kept for comparison)

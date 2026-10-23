"""Version labels that key evaluations and recommendations (SPEC D10). Bump MATCH_PROMPT_VERSION
when the E3 prompt, its schema or E4 validation changes (new evaluations); bump
VERDICT_RULE_VERSION with a new verdict_rules/vN.yaml when E5/E6 change (recompute only, no
model calls)."""

MATCH_PROMPT_VERSION = "m1-v1"
VERDICT_RULE_VERSION = "v1"

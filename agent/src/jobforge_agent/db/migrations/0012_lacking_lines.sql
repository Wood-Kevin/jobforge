-- 0012_lacking_lines: posting lines the person answered "I don't have this" to on a "Needs your input" question
-- (2026-10-29; Kevin: "There is no option to dismiss a question for needs your input if I don't have the skill").
-- A JSON array of the posting's own lines beside the completeness flags, because it is the same kind of statement as
-- cert_list_complete: an absence the person confirms, which code may read as a gap. E4 (evaluation/validate.py)
-- reads a requirement whose line is one of these (same words, any case or spacing) as an evidenced gap on any job.
-- Whole lines, not short names: a short name ("SQL") would reach lines the person never saw (the parked "Things you
-- don't have" list, DEFERRED_COMPLEXITY). Never sent to the model: the E3 request stays byte-identical, so the
-- re-evaluation after an answer reuses the stored answers and costs no model calls.
ALTER TABLE profile_completeness ADD COLUMN lacking TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(lacking) AND json_type(lacking) = 'array');

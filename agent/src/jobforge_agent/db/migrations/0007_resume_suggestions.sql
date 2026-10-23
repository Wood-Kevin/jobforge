-- 0007_resume_suggestions: profile items suggested from an uploaded resume (P1.8c) go to the same
-- inbox as E3's inferred claims, unconfirmed until the user accepts one (SPEC D2: the inbox is never
-- in a snapshot, so nothing can cite a suggestion).
-- proposed_by_resume_id has no foreign key on purpose: deleting a resume must neither fail because
-- suggestions point at it nor delete suggestions the user already accepted. The quote each
-- suggestion was checked against stays in `evidence`.
ALTER TABLE inferred_claims ADD COLUMN proposed_by_resume_id INTEGER;
-- Item fields beyond kind and statement (start_date, end_date, attributes), applied on accept.
ALTER TABLE inferred_claims ADD COLUMN proposed_fields TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(proposed_fields));

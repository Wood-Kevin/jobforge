-- 0009_employer_origin: where a watched company came from (P1.10). 'directory' rows are added by the
-- agent from the built-in company list (discovery/directory.json) and follow the "watch the built-in
-- list" switch; 'user' rows were added by the person (link, name, watchlist file) and the switch never
-- touches them. Existing rows were all added by the person.
ALTER TABLE employers ADD COLUMN origin TEXT NOT NULL DEFAULT 'user' CHECK (origin IN ('user', 'directory'));

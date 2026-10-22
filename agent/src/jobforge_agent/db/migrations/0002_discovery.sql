-- 0002_discovery: per-employer job ownership and the reviewed Phase 1 sources (P1.3).

-- Missing-posting detection is per employer: a job absent from its employer's complete
-- listing counts toward closure. Nullable because ALTER TABLE ADD COLUMN cannot add a
-- NOT NULL foreign key; discovery always sets it.
ALTER TABLE jobs ADD COLUMN employer_id INTEGER REFERENCES employers(id);
CREATE INDEX jobs_by_employer_open ON jobs (employer_id) WHERE closed_at_ms IS NULL;

-- Terms review recorded 2026-10-21 (ARCHITECTURE Appendix A, "(c, cont.) Terms review").
-- Greenhouse: public GET endpoints need no auth; robots.txt disallows only /embed/.
-- Lever: public GET, only POSTs rate limited; robots.txt Crawl-delay: 1 (honored via spacing).
-- Ashby: public GET; robots.txt answers 401 (no rules, RFC 9309); CDN max-age 60 s.
INSERT INTO sources (kind, host, enabled, terms_reviewed_on, terms_note, default_interval_s, min_interval_s,
                     max_requests_per_hour, max_concurrency, request_spacing_ms, crawl_delay_ms, conditional_requests)
VALUES
  -- Greenhouse budget 300/h: the imported watchlist has 58 Greenhouse employers, 232 polls/h at the
  -- 15 min default; 120/h would silently stretch them to ~30 min. 300/h averages one request per
  -- 12 s to this host (2 s minimum spacing), and unchanged boards answer 304 with headers only.
  ('greenhouse', 'boards-api.greenhouse.io', 1, '2026-10-21',
   'Job Board API docs: GET endpoints public, no auth; no rate limit stated. robots.txt disallows /embed/ only.',
   900, 300, 300, 1, 2000, NULL, 'etag'),
  ('lever', 'api.lever.co', 1, '2026-10-21',
   'postings-api README: GET public; only application POSTs rate limited; postings may be scraped by third parties. robots.txt Crawl-delay: 1.',
   900, 300, 120, 1, 2000, 1000, 'etag'),
  ('ashby', 'api.ashbyhq.com', 1, '2026-10-21',
   'Public job posting API docs: published postings via GET; no rate limit stated. robots.txt 401 = no rules (RFC 9309). CDN max-age 60 s.',
   900, 300, 120, 1, 2000, NULL, 'etag');

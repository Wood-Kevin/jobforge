-- 0006_applications: one application per job (P1.8). Status changes are rows in the append-only
-- application_events table (trigger from 0001), written in the same user-action transaction.
CREATE UNIQUE INDEX applications_one_per_job ON applications (job_id);

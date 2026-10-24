// Every agent route the window uses, in one place. Types are written by hand: the agent's routes
// return plain JSON objects without response models, so there is no OpenAPI schema to generate
// them from (SPEC D14 corrected in P1.8). agent/tests/test_views.py reads this file and fails if a
// call names a route or method the agent does not serve, which catches renames.
import { agentCall, agentUpload } from "./agent";

export type Health = {
  version: string; protocol_version: number; pid: number; run_id: number; schema: number; elevated: boolean;
  discovery_paused: boolean; uptime_s: number; last_maintenance_error: string | null; directory_error: string | null; data_dir: string;
};
export type Gate = Record<string, { up: boolean | null; reason: string | null; next_check_ms: number }>;
export type QueueStatus = { counts: Record<string, number>; pending_waiting_for_disabled_config: number; configs_with_work: number[]; inference: Gate };
export type DiscoveryStatus = {
  paused: boolean; jobs_open: number; jobs_total: number; polls_in_flight: number;
  sources: { kind: string; host: string; enabled: boolean; backoff_until_ms: number | null; backoff_reason: string | null;
    requests_last_hour: number; max_requests_per_hour: number; bytes_last_hour: number; polls_last_hour: number; not_modified_last_hour: number }[];
  employers: { id: number; source: string; slug: string; name: string; enabled: boolean; status: string; interval_s: number;
    next_poll_in_s: number | null; last_success_age_s: number | null; last_attempt_age_s: number | null;
    consecutive_failures: number | null; last_outcome: string | null; last_error: string | null }[];
  // The built-in company list, as counts (its rows are not listed; P1.10).
  built_in: { watching: boolean; total: number; enabled: number; dead: number; blocked: number; failing: number;
    checked_once: number; last_success_age_s: number | null };
};
export type FilterSummary = { profile_version: number | null; ruleset?: string; verdicts?: Record<string, number>;
  rejected_by_primary_category?: Record<string, number>; analyses_pending?: number };

export type Category = "APPLY" | "STRETCH" | "REVIEW" | "SKIP";
export type RecSummary = { recommendation: Category; eligibility: string; fit: string; opportunity: string; rank_key: number | null;
  current: boolean; newer_evaluation_pending: boolean };
export type JobRow = { id: number; title: string; company: string; location: string | null; work_mode: string | null; posted_at: string | null;
  first_seen_at_ms: number; filter_verdict: "pass" | "reject" | null; filter_primary: string | null; override: boolean;
  application_status: string | null; recommendation: RecSummary | null };
export type JobList = { view: string; total: number; jobs: JobRow[]; search_profile: { id: number; version: number } | null };

export type Match = { matched_by: string; match_type: string; basis: string; citations: number[]; bridge: string | null;
  transfer_limits: string | null; gap_size: Record<string, unknown> | null; gap_materiality: string; flags: { flag: string; [k: string]: unknown }[] };
export type EvidenceRow = { requirement_id: number; quote: string; class: string; status: string; origin: string; eligibility_type: string | null; match: Match | null };
export type Reason = { rule: number; name: string; detail: string; questions?: { kind: string; type?: string; requirement_id?: number; question: string }[] };
export type Recommendation = { recommendation: Category; eligibility: string; fit: string; opportunity: string; verdict_rule_version: string;
  eligibility_reasons: { requirement_id: number; quote: string; type: string; status: string; origin: string; outcome: string; basis: string; effect: string; why: string; question?: string }[];
  fit_counts: Record<string, unknown> & { rule: string }; opportunity_factors: { factor: string; value: string; detail: string }[];
  decisive_reasons: Reason[]; computed_at_ms: number };
export type Evaluation = { job_id: number; evaluation: { id: number; profile_snapshot_id: number; model_id_reported: string | null; match_prompt_version: string; created_at_ms: number };
  recommendation: Recommendation | null; evidence: EvidenceRow[]; current: boolean; newer_evaluation_pending: boolean;
  cited_items: Record<string, { kind: string; statement: string; status: string }> };
export type Application = { id: number; job_id: number; status: string; notes: string | null; created_at_ms: number;
  events: { from_status: string | null; to_status: string; at_ms: number; note: string | null }[] };
export type JobDetail = { id: number; title: string; company: string; location: string | null; work_mode: string | null;
  salary: { min: number | null; max: number | null; period: string } | null; posted_at: string | null; first_seen_at_ms: number;
  closed_at_ms: number | null; posting_versions: number; listing_url: string | null; apply_url: string | null; employer: string; source: string;
  description_html: string | null; description_text: string | null; filter: { verdict: string; reasons: { primary: string | null; failed: { rule: string; category: string; detail: string | null }[] } } | null;
  override: boolean; analysis: { id: number; status: string; last_error: string | null } | null; evaluation: Evaluation | null; application: Application | null;
  golden_label: GoldenLabel | null };
export type GoldenLabel = { id: number; recommendation: Category; eligibility: string | null; obvious: boolean; misread_requirement_ids: number[];
  note: string | null; labeled_by: string; shown_recommendation_id: number | null; created_at_ms: number };
export type GoldenVersion = { model: string; extraction_prompt: string; interpreter: string; match_prompt: string; rules: string;
  labeled_with_answer: number; false_skip: number[]; false_apply: number[]; review_share: number | null; review_count: number;
  review_labeled_obvious: number[]; eligibility_labeled: number; eligibility_agree: number; confusion: Record<Category, Record<Category, number>> };
export type GoldenReport = { labels: number; target: [number, number]; provisional_unreviewed: number; labels_without_system_answer: number[];
  misread_requirements: number; versions: GoldenVersion[] };

export type Item = { id: number; kind: string; statement: string; attributes: Record<string, unknown>; start_date: string | null;
  end_date: string | null; years: number | null; status: "verified" | "self_reported"; source: string; verified_at_ms: number | null };
export type Completeness = { cert_list_complete: boolean; eligibility_facts_complete: boolean; updated_at_ms: number | null };
export type Schema = { kinds: string[]; eligibility: Record<string, Record<string, { type: string; required: boolean }>>; multiple_allowed: string[]; application_statuses: string[] };
export type Claim = { id: number; statement: string; kind: string; rationale: string | null; state: string; proposed_by_evaluation_id: number | null; created_at_ms: number;
  proposed_by_resume_id: number | null; proposed_fields: { start_date?: string; end_date?: string | null; attributes?: Record<string, string> };
  evidence: { quote?: string; [k: string]: unknown } };
export type SuggestReport = { proposed: number; added: number; already_known: number; quote_not_found: number; bad_kind: number; repeated: number; seconds: number };
export type Resume = { id: number; label: string | null; original_filename: string; mime: string; sha256: string; bytes: number; text_chars: number; uploaded_at_ms: number };
export type ReviewGroup = { kind: string; type: string | null; example: string; jobs: { job_id: number; title: string; company: string; question: string; current: boolean }[] };
export type ModelConfig = { id: number; name: string; model_id: string; base_url: string; enabled: boolean; tested_at_ms: number | null; default: boolean };
export type ApplicationRow = { id: number; job_id: number; status: string; notes: string | null; title: string; company: string; job_closed: boolean; last_change_ms: number };

export type SetupStatus = {
  employers: { done: boolean; enabled: number }; search_profile: { done: boolean; version: number | null };
  model: { done: boolean; name: string | null; model_id: string | null; base_url: string | null; tested: boolean };
  profile: { done: boolean; items: number; facts: number; facts_verified: number }; complete: boolean };
export type SearchForm = { titles: string[]; skip_seniority: string[]; skip_fields: string[]; home_state: string; home_towns: string[];
  salary_floor: number | null; pay_target: number | null; work_modes: Record<string, string>; favorite_titles: string[];
  favorite_levels: string[]; max_age_days: number | null; has_profile?: boolean; kept_patterns?: Record<string, string[]> };
export type EmployerAdd = { added: number; existing: number; watched_again: number; skipped_unsupported: string[]; not_understood?: string[];
  not_found?: string[]; check_failed?: string[]; not_checked?: string[] };

const q = (v: string) => encodeURIComponent(v);

export const api = {
  health: () => agentCall<Health>("GET", "/v1/health"),
  pauseDiscovery: () => agentCall<Health>("POST", "/v1/discovery/pause"),
  resumeDiscovery: () => agentCall<Health>("POST", "/v1/discovery/resume"),
  discoveryStatus: () => agentCall<DiscoveryStatus>("GET", "/v1/discovery/status"),
  pollNow: (employerId: number) => agentCall<{ due: boolean }>("POST", `/v1/admin/employers/${employerId}/poll-now`),
  filterSummary: () => agentCall<FilterSummary>("GET", "/v1/filter/summary"),
  analysisStatus: () => agentCall<QueueStatus>("GET", "/v1/analysis/status"),
  evaluationStatus: () => agentCall<QueueStatus>("GET", "/v1/evaluation/status"),

  jobs: (view: "recommended" | "rejected" | "all", search: string, limit = 200, offset = 0) =>
    agentCall<JobList>("GET", `/v1/jobs?view=${view}&limit=${limit}&offset=${offset}${search ? `&q=${q(search)}` : ""}`),
  job: (id: number) => agentCall<JobDetail>("GET", `/v1/jobs/${id}`),
  evaluateAnyway: (id: number, note: string) => agentCall<{ queued: boolean; waiting_for_text: boolean }>("POST", `/v1/jobs/${id}/evaluate-anyway`, { note }),
  setApplication: (id: number, fields: { status?: string; notes?: string; note?: string }) =>
    agentCall<Application>("PUT", `/v1/jobs/${id}/application`, fields),
  applications: () => agentCall<ApplicationRow[]>("GET", "/v1/applications"),
  reviewQuestions: () => agentCall<ReviewGroup[]>("GET", "/v1/review-questions"),
  setGoldenLabel: (id: number, fields: { recommendation: Category; eligibility?: string | null; obvious?: boolean; misread_requirement_ids?: number[]; note?: string | null }) =>
    agentCall<GoldenLabel>("POST", `/v1/jobs/${id}/golden-label`, fields),
  withdrawGoldenLabel: (id: number) => agentCall<{ withdrawn: boolean }>("POST", `/v1/jobs/${id}/golden-label/withdraw`),
  goldenReport: () => agentCall<GoldenReport>("GET", "/v1/golden/report"),
  recompute: () => agentCall<Record<string, unknown>>("POST", "/v1/recompute"),

  items: () => agentCall<Item[]>("GET", "/v1/experience"),
  createItem: (fields: Partial<Item>) => agentCall<{ id: number }>("POST", "/v1/experience", fields),
  updateItem: (id: number, fields: Partial<Item>) => agentCall<{ verification_cleared: boolean }>("PATCH", `/v1/experience/${id}`, fields),
  deleteItem: (id: number) => agentCall<{ deleted: number }>("DELETE", `/v1/experience/${id}`),
  verifyItem: (id: number, note: string | null) => agentCall<Item>("POST", `/v1/experience/${id}/verify`, note ? { note } : {}),
  unverifyItem: (id: number) => agentCall<Item>("POST", `/v1/experience/${id}/unverify`),
  completeness: () => agentCall<Completeness>("GET", "/v1/profile/completeness"),
  setCompleteness: (fields: Partial<Completeness>) => agentCall<Completeness>("PUT", "/v1/profile/completeness", fields),
  schema: () => agentCall<Schema>("GET", "/v1/profile/schema"),
  claims: (state: "open" | "accepted" | "rejected") => agentCall<Claim[]>("GET", `/v1/inferred-claims?state=${state}`),
  acceptClaim: (id: number, edits: { statement?: string; kind?: string }) => agentCall<{ item_id: number }>("POST", `/v1/inferred-claims/${id}/accept`, edits),
  rejectClaim: (id: number) => agentCall<{ rejected: number }>("POST", `/v1/inferred-claims/${id}/reject`),
  resumes: () => agentCall<Resume[]>("GET", "/v1/resumes"),
  uploadResume: (filename: string, bytes: Uint8Array) => agentUpload<Resume>(`/v1/resumes?filename=${q(filename)}`, bytes),
  deleteResume: (id: number) => agentCall<{ deleted: number }>("DELETE", `/v1/resumes/${id}`),
  suggestItems: (id: number) => agentCall<SuggestReport>("POST", `/v1/resumes/${id}/suggest-items`),

  setupStatus: () => agentCall<SetupStatus>("GET", "/v1/setup/status"),
  searchForm: () => agentCall<SearchForm>("GET", "/v1/search-profile/form"),
  saveSearchForm: (form: SearchForm) => agentCall<{ version: number }>("POST", "/v1/search-profile/form", form),
  importScannerConfig: (text: string) => agentCall<{ version: number }>("POST", "/v1/search-profile/import-scanner", { text }),
  addEmployers: (text: string) => agentCall<EmployerAdd>("POST", "/v1/employers/add", { text }),
  importWatchlist: (text: string) => agentCall<EmployerAdd>("POST", "/v1/employers/import-watchlist", { text }),
  enableEmployer: (id: number) => agentCall<{ enabled: boolean }>("POST", `/v1/employers/${id}/enable`),
  disableEmployer: (id: number) => agentCall<{ enabled: boolean }>("POST", `/v1/employers/${id}/disable`),
  setBuiltInWatch: (watch: boolean) => agentCall<{ watching: boolean; changed: number }>("POST", "/v1/employers/built-in", { watch }),
  modelConfigs: () => agentCall<ModelConfig[]>("GET", "/v1/model-configs"),
  testModelConfig: (id: number) => agentCall<{ ok: boolean; error?: string; seconds?: number }>("POST", `/v1/model-configs/${id}/test`),
  enableModelConfig: (id: number) => agentCall<unknown>("POST", `/v1/model-configs/${id}/enable`),
  disableModelConfig: (id: number) => agentCall<unknown>("POST", `/v1/model-configs/${id}/disable`),
};

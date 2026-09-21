export type AnalysisSummary = {
  id: string;
  created_at: string;
  seniority_level: string | null;
  language_requirement: string | null;
  must_have: string[];
  nice_to_have: string[];
};
export type MatchSummary = {
  id: string;
  analysis_id: string | null;
  overall_score: number;
  confidence: string;
  recommendation: string;
  matched_requirements: string[];
  partial_matches: string[];
  gaps: string[];
  critical_gaps: string[];
  created_at: string;
};
export type Job = {
  id: string;
  source: string;
  source_id: string;
  source_lifecycle_status: string;
  source_enabled: boolean;
  title: string;
  source_job_url: string;
  canonical_url: string | null;
  description: string | null;
  location_raw: string | null;
  city: string | null;
  country_code: string | null;
  employment_type: string | null;
  availability_status: string;
  first_seen_at: string;
  last_seen_at: string;
  date_posted: string | null;
  seen_at: string | null;
  application_status: string | null;
  is_applied: boolean;
  company_id: string | null;
  company_name: string | null;
  language: string | null;
  seniority_level: string | null;
  is_remote: boolean | null;
  is_hybrid: boolean | null;
  starred: boolean;
  analysis_status: string;
  latest_analysis: AnalysisSummary | null;
  latest_match: MatchSummary | null;
  cover_letter: { eligible: boolean; reason: string };
  match_score: number | null;
  recommendation: string | null;
  matched_requirements: string[];
  gaps: string[];
};
export type Page = {
  page: number;
  page_size: number;
  total: number;
  items: Job[];
};
export type Facet = { value: string; count: number };
export type SearchResponse = {
  items: Job[];
  total: number;
  facets: Record<string, Facet[]>;
  query: Record<string, string | null>;
};
export type AgenticSearchResponse = {
  mode: "deterministic" | "agentic_fallback";
  trigger_reason: string;
  original_query: string;
  refined_query: string | null;
  interpreted_intent: string | null;
  explanation: string | null;
  tool_calls: { name: string }[];
  items: Job[];
  error_code: string | null;
  trace: Record<string, unknown>;
};
export type Run = {
  id: string;
  started_at: string;
  ended_at: string | null;
  status: string;
  sources_total: number;
  sources_success: number;
  sources_partial: number;
  sources_failed: number;
  jobs_discovered: number;
  jobs_new: number;
  jobs_analyzed: number;
  jobs_matched: number;
  jobs_quarantined: number;
};
export type SourceHealth = {
  source_id: string;
  display_name: string;
  health_status: string;
  enabled: boolean;
  last_successful_run: string | null;
  jobs_discovered: number;
  jobs_persisted: number;
  failures: number;
};
export type SourceSummary = {
  id: string;
  source_id: string;
  display_name: string;
  lifecycle_status: string;
  readiness_status: string;
  health_status: string;
  canonical_url: string | null;
  enabled: boolean;
};
export type Company = {
  id: string;
  name: string;
  domain: string;
  country: string;
  lifecycle_status: string;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  sources: SourceSummary[];
  source_count: number;
  primary_careers_url: string | null;
  last_successful_run: string | null;
  job_count: number;
  unseen_job_count: number;
  recommended_job_count: number;
  health_status: string;
};
export type Source = SourceSummary & {
  company_id: string | null;
  official_jobs_url: string;
  allowed_domains: string[];
  robots_txt_url: string | null;
  crawl_delay_seconds: number;
  boundary_fingerprint: string | null;
  last_validated_at: string | null;
  approved_preview_version: number | null;
  approved_at: string | null;
  archived_at: string | null;
  last_successful_run: string | null;
  created_at: string;
  updated_at: string;
};
export type Preview = {
  id: string;
  source_id: string;
  version: number;
  status: string;
  boundary_fingerprint: string;
  source_url: string | null;
  canonical_url: string | null;
  lifecycle_status: string;
  readiness_status: string;
  validation_summary: Record<string, unknown>;
  created_at: string;
  expires_at: string | null;
  approved_at: string | null;
  discarded_at: string | null;
};
export type Analysis = {
  id: string;
  job_id: string;
  run_id: string | null;
  model: string;
  prompt_version: string;
  analysis_result: Record<string, unknown>;
  created_at: string;
};
export type Match = {
  id: string;
  job_id: string;
  analysis_id: string | null;
  overall_score: number;
  confidence: string;
  recommendation: "APPLY" | "CONSIDER" | "LOW_PRIORITY" | "SKIP" | string;
  critical_gaps: string[] | null;
  reasoning: string | null;
  evidence: Record<string, unknown> | null;
  model: string;
  prompt_version: string;
  created_at: string;
};
export type Application = {
  id: string;
  job_id: string;
  status: string;
  notes: string | null;
  applied_at: string | null;
  created_at: string;
  updated_at: string;
};
export type History = { analyses: Analysis[]; matches: Match[] };
export type CoverLetter = {
  id: string;
  job_id: string;
  application_id: string | null;
  match_result_id: string | null;
  content: string;
  model: string;
  prompt_version: string;
  grounding_valid: boolean | null;
  grounding_notes: string | null;
  human_reviewed: boolean;
  human_notes: string | null;
  created_at: string;
};
export type WorkflowResult = { job: Job; analysis: Analysis; match: Match };
export type WorkflowDiscoveryItem = {
  job: Job;
  analysis: Analysis | null;
  match: Match | null;
};
export type Skill = {
  name: string;
  level: "Beginner" | "Intermediate" | "Advanced" | "Expert";
};
export type CandidateFact = {
  evidence_id: string;
  section: string;
  claim: string;
  classification: string;
  confidence: string;
};
export type ProfilePreferences = {
  target_roles: string[];
  locations: string[];
  work_modes: string[];
  job_languages: string[];
  cover_letter_tone: string;
  minimum_fit: number;
};
export type Profile = {
  version?: string;
  rescored_jobs?: number;
  recalculated_matches?: number;
  stale_matches?: number;
  name: string;
  headline: string;
  summary: string;
  email: string;
  phone: string;
  seniority: string;
  years_experience: number;
  skills: Skill[];
  target_roles: string[];
  locations: string[];
  languages: string[];
  work_modes: string[];
  cover_letter_tone: string;
  minimum_fit: number;
  facts?: { version: string; evidence_count: number; items: CandidateFact[] };
  preferences?: ProfilePreferences;
};

const base = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";
const demoMode = (import.meta.env.VITE_APP_MODE ?? "demo") === "demo";

async function errorMessage(response: Response): Promise<string> {
  if (response.status === 401)
    return "Dashboard authorization failed. Check the configured dashboard token.";
  if (response.status === 403)
    return "Human authorization is required for this action.";
  let detail = `Request failed (${response.status})`;
  try {
    const err = await response.json();
    if (err?.detail) detail = err.detail;
  } catch {
    // ignore non-JSON error bodies
  }
  return detail;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${base}${path}`);
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}

async function post<T>(path: string, body: object): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Actor-Type": "HUMAN" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}

async function patch<T>(path: string, body: object): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-Actor-Type": "HUMAN" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}

async function remove<T>(path: string): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    method: "DELETE",
    headers: { "X-Actor-Type": "HUMAN" },
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}

export const dashboardApi = {
  jobs: (
    params: Record<string, string | number | boolean | undefined> = {},
  ) => {
    const query = new URLSearchParams({ page: "1", page_size: "50" });
    for (const [key, value] of Object.entries(params))
      if (value !== undefined) query.set(key, String(value));
    return get<Page>(`/api/dashboard/jobs?${query}`);
  },
  searchJobs: (params: Record<string, string | undefined> = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params))
      if (value) query.set(key, value);
    return get<SearchResponse>(`/api/search/jobs?${query}`);
  },
  agenticSearchAvailable: () =>
    get<{ enabled: boolean }>("/api/search/agentic/status"),
  agenticSearch: (query: string) =>
    post<AgenticSearchResponse>("/api/search/agentic", { query }),
  runs: () => get<Run[]>("/api/dashboard/runs?limit=25"),
  sources: () => get<SourceHealth[]>("/api/dashboard/source-health"),
  applications: () =>
    get<Application[]>("/api/dashboard/applications?page=1&page_size=50"),
  history: async (jobId: string) => {
    if (!demoMode) await post<Job>(`/api/dashboard/jobs/${jobId}/seen`, {});
    return get<History>(`/api/dashboard/jobs/${jobId}/history`);
  },
  createApplication: (jobId: string, notes?: string) =>
    post<Application>(`/api/applications/jobs/${jobId}`, {
      target_state: "INTERESTING",
      notes,
    }),
  transitionApplication: (
    applicationId: string,
    targetState: string,
    notes?: string,
  ) =>
    post<Application>(`/api/applications/${applicationId}/transition`, {
      target_state: targetState,
      notes,
    }),
  generateCoverLetter: (jobId: string, matchId: string) =>
    post<CoverLetter>("/api/cover-letters/generate", {
      job_id: jobId,
      match_id: matchId,
    }),
  reviewCoverLetter: (
    letterId: string,
    humanReviewed: boolean,
    humanNotes?: string,
  ) =>
    post<CoverLetter>(`/api/cover-letters/${letterId}/review`, {
      human_reviewed: humanReviewed,
      human_notes: humanNotes ?? null,
    }),
  associateCoverLetter: (applicationId: string, coverLetterId: string) =>
    post<Application>(`/api/applications/${applicationId}/cover-letter`, {
      cover_letter_id: coverLetterId,
    }),
  coverLetters: (jobId: string) =>
    get<CoverLetter[]>(`/api/cover-letters/jobs/${jobId}`),
  analyzeUrl: (url: string) =>
    post<WorkflowResult>("/api/workflow/analyze", { url }),
  discoverJobs: (careersUrl: string) =>
    post<WorkflowDiscoveryItem[]>("/api/workflow/discover", {
      careers_url: careersUrl,
    }),
  companies: () => get<Company[]>("/api/sources/companies"),
  createCompany: (body: { name: string; domain: string; country: string }) =>
    post<Company>("/api/sources/companies", body),
  createSource: (
    companyId: string,
    body: {
      source_id: string;
      display_name: string;
      official_jobs_url: string;
      canonical_url: string;
      allowed_domains: string[];
      robots_txt_url?: string;
      crawl_delay_seconds: number;
    },
  ) => post<Source>(`/api/sources/companies/${companyId}/sources`, body),
  source: (sourceId: string) => get<Source>(`/api/sources/${sourceId}`),
  onboardSource: (sourceId: string, url?: string) =>
    post<Preview>(`/api/sources/${sourceId}/onboarding`, { url: url || null }),
  previews: (sourceId: string) =>
    get<Preview[]>(`/api/sources/${sourceId}/previews`),
  approveSource: (sourceId: string, previewVersion: number) =>
    post<Source>(`/api/sources/${sourceId}/approve`, {
      preview_version: previewVersion,
    }),
  revalidateSource: (sourceId: string) =>
    post<Preview>(`/api/sources/${sourceId}/revalidate`, {}),
  archiveSource: (sourceId: string) =>
    remove<Source>(`/api/sources/${sourceId}`),
  archiveCompany: (companyId: string) =>
    remove<Company>(`/api/sources/companies/${companyId}`),
  updateCompany: (
    companyId: string,
    body: { name?: string; domain?: string; country?: string },
  ) => patch<Company>(`/api/sources/companies/${companyId}`, body),
  updateSource: (
    sourceId: string,
    body: Partial<
      Pick<
        Source,
        | "display_name"
        | "official_jobs_url"
        | "canonical_url"
        | "allowed_domains"
        | "robots_txt_url"
        | "crawl_delay_seconds"
      >
    >,
  ) => patch<Source>(`/api/sources/${sourceId}`, body),
  starJob: (jobId: string, starred: boolean) =>
    patch<Job>(`/api/dashboard/jobs/${jobId}/star`, { starred }),
  profile: () => get<Profile>("/api/profile"),
  saveProfile: (body: {
    facts?: { confirmed_additions: string[] };
    preferences?: ProfilePreferences;
  }) => {
    return fetch(`${base}/api/profile`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Actor-Type": "HUMAN" },
      body: JSON.stringify(body),
    }).then(async (response) => {
      if (!response.ok) throw new Error(await errorMessage(response));
      return response.json() as Promise<Profile>;
    });
  },
  runDaily: () => post<{ status: string }>("/api/runs/daily", {}),
  schedule: () =>
    get<{ enabled: boolean; time: string; timezone: string }>(
      "/api/runs/schedule",
    ),
  saveSchedule: (enabled: boolean, time: string) =>
    fetch(`${base}/api/runs/schedule`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Actor-Type": "HUMAN" },
      body: JSON.stringify({ enabled, time }),
    }).then(async (response) => {
      if (!response.ok) throw new Error(await errorMessage(response));
      return response.json();
    }),
};

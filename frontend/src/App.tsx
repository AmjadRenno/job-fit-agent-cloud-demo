import { ReactNode, useEffect, useState } from "react";
import {
  AgenticSearchResponse,
  Company,
  dashboardApi,
  Job,
  Match,
  Profile,
  Run,
  SourceHealth,
} from "./api";

type View = "dashboard" | "jobs" | "starred" | "companies" | "profile";
type JobTab = "recommended" | "unseen" | "applied" | "all";

const blank: Profile = {
  name: "Candidate",
  headline: "",
  summary: "",
  email: "",
  phone: "",
  seniority: "Unspecified",
  years_experience: 0,
  skills: [],
  target_roles: [],
  locations: [],
  languages: [],
  work_modes: [],
  cover_letter_tone: "Direct & professional",
  minimum_fit: 60,
};
const applicationLabels: Record<string, string> = {
  DISCOVERED: "New",
  INTERESTING: "Interested",
  TO_APPLY: "To apply",
  APPLIED: "Applied",
  INTERVIEW: "Interview",
  OFFER: "Offer",
  REJECTED: "Rejected",
  CLOSED: "Closed",
  IGNORED: "Ignored",
};
const sourceLabels: Record<string, string> = {
  DRAFT: "Draft",
  ONBOARDING: "Inspecting",
  PREVIEW_READY: "Preview Ready",
  ACTIVE: "Active",
  REVALIDATION_REQUIRED: "Revalidation Required",
  DISABLED: "Disabled",
  BLOCKED: "Blocked",
  ARCHIVED: "Archived",
  UNKNOWN: "Unknown",
  READY: "Ready",
};

function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "blue" | "danger";
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <b>{title}</b>
      <span>{children}</span>
      {action}
    </div>
  );
}
function FitScore({ job }: { job: Job }) {
  return job.latest_match && job.match_score !== null ? (
    <span className="fit-score">
      <b>{(job.match_score / 10).toFixed(1)}</b>
      <small>/10</small>
    </span>
  ) : (
    <span className="fit-score muted">
      <small>Not analyzed</small>
    </span>
  );
}
function formatDate(value: string | null | undefined) {
  return value
    ? new Intl.DateTimeFormat("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      }).format(new Date(value))
    : null;
}
function recency(job: Job) {
  return job.date_posted
    ? `Posted ${formatDate(job.date_posted)}`
    : `Found ${formatDate(job.first_seen_at)}`;
}
function mode(job: Job) {
  return job.is_remote ? "Remote" : job.is_hybrid ? "Hybrid" : null;
}

function Sidebar({
  view,
  setView,
  counts,
}: {
  view: View;
  setView: (view: View) => void;
  counts: { jobs: number; starred: number };
}) {
  const links: Array<[View, string, string]> = [
    ["dashboard", "Dashboard", "▦"],
    ["jobs", "Jobs", "☰"],
    ["starred", "Starred", "☆"],
    ["companies", "Companies", "▤"],
    ["profile", "My Profile", "♙"],
  ];
  return (
    <aside className="sidebar">
      <button className="brand" onClick={() => setView("dashboard")}>
        <i>◎</i>
        <span>
          <b>job–fit–agent</b>
          <small>Your signal, not noise</small>
        </span>
      </button>
      <label>WORKSPACE</label>
      <nav>
        {links.map(([id, label, icon]) => (
          <button
            key={id}
            className={view === id ? "active" : ""}
            onClick={() => setView(id)}
          >
            <i>{icon}</i>
            <span>{label}</span>
            {id === "jobs" && <em>{counts.jobs}</em>}
            {id === "starred" && <em>{counts.starred}</em>}
          </button>
        ))}
      </nav>
      <div className="side-note">
        <b>⚡ Human in control</b>
        <span>The system finds and explains. No applications are submitted by this demo.</span>
      </div>
    </aside>
  );
}

function displayEnum(value: string) {
  return value.replace(/_/g, " ");
}
function JobRow({
  job,
  onOpen,
  onStar,
}: {
  job: Job;
  onOpen: () => void;
  onStar: () => void;
}) {
  const state = job.application_status
    ? (applicationLabels[job.application_status] ?? job.application_status)
    : "Synthetic";
  return (
    <article className="job-row">
      <button
        className="score-button"
        aria-label={`Open ${job.title}`}
        onClick={onOpen}
      >
        <FitScore job={job} />
      </button>
      <button className="job-main" onClick={onOpen}>
        <b>{job.title}</b>
        <span>
          {job.company_name ?? job.source}
          {job.location_raw ? ` · ${job.location_raw}` : ""}
        </span>
        <small>{recency(job)}</small>
        <div className="badges">
          {job.language && <Badge>{job.language}</Badge>}
          {mode(job) && <Badge tone="good">{mode(job)}</Badge>}
          <Badge tone={job.application_status ? "blue" : "neutral"}>
            {state}
          </Badge>
          {job.analysis_status !== "COMPLETED" && (
            <Badge tone="warn">Not analyzed</Badge>
          )}
        </div>
      </button>
      <div className="job-match">
        {job.latest_match ? (
          <>
            <div className="badges">
              {job.matched_requirements.slice(0, 2).map((item) => (
                <Badge key={item} tone="good">
                  ✓ {item}
                </Badge>
              ))}
              {job.gaps.slice(0, 1).map((item) => (
                <Badge key={item} tone="warn">
                  Gap: {item}
                </Badge>
              ))}
            </div>
            <small>
              {job.recommendation && displayEnum(job.recommendation)}
            </small>
          </>
        ) : (
          <small>Analysis unavailable for this stored job</small>
        )}
      </div>
      <button
        className={`star ${job.starred ? "on" : ""}`}
        aria-label={job.starred ? "Remove from Starred" : "Add to Starred"}
        onClick={onStar}
      >
        {job.starred ? "★" : "☆"}
      </button>
    </article>
  );
}

function PageTitle({
  eyebrow,
  title,
  detail,
  action,
}: {
  eyebrow: string;
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-title">
      <div>
        <p>{eyebrow}</p>
        <h1>{title}</h1>
        <span>{detail}</span>
      </div>
      {action}
    </header>
  );
}

function Dashboard({
  recommended,
  jobs,
  starred,
  companies,
  sources,
  runs,
  onOpen,
  onStar,
  onView,
}: {
  recommended: Job[];
  jobs: Job[];
  starred: Job[];
  companies: Company[];
  sources: SourceHealth[];
  runs: Run[];
  onOpen: (job: Job) => void;
  onStar: (job: Job) => void;
  onView: (view: View) => void;
}) {
  const healthy = sources.filter(
    (source) => source.health_status === "HEALTHY",
  ).length;
  const sourceText = sources.length
    ? `${healthy}/${sources.length} sources healthy`
    : "Source health unknown";
  return (
    <>
      <section className="hero">
        <div>
          <p>● YOUR DAILY SIGNAL</p>
          <h1>
            A quieter way to find
            <br />
            your next good role.
          </h1>
          <span>
            {recommended.length
              ? `${recommended.length} recommended role${recommended.length === 1 ? "" : "s"} meet your current threshold.`
              : "No role currently meets your threshold."}
          </span>
        </div>
        <span>Public demo: synthetic data and read-only backend.</span>
      </section>
      <div className="metrics">
        <Metric
          label="RECOMMENDED"
          value={recommended.length}
          detail="At or above your fit threshold"
        />
        <Metric
          label="DEMO JOBS"
          value={jobs.length}
          detail="Persisted synthetic roles"
        />
        <Metric
          label="STARRED"
          value={starred.length}
          detail="Saved for later"
        />
        <Metric
          label="SOURCES"
          value={sources.length}
          detail={sources.length ? `${healthy}/${sources.length} healthy` : "Source health unavailable"}
        />
      </div>
      <div className="dash-grid">
        <section className="panel">
          <header>
            <span>
              <b>Your best matches</b>
              <small>Only roles that meet your current minimum fit.</small>
            </span>
            <button onClick={() => onView("jobs")}>Review jobs →</button>
          </header>
          {recommended.length ? (
            recommended
              .slice(0, 5)
              .map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  onOpen={() => onOpen(job)}
                  onStar={() => onStar(job)}
                />
              ))
          ) : (
            <EmptyState
              title="No recommended jobs yet"
              action={
                <button onClick={() => onView("jobs")}>Review new jobs</button>
              }
            >
              New and lower-fit roles stay available in Jobs.
            </EmptyState>
          )}
        </section>
        <aside>
          <section className="panel company-mini">
            <header>
              <b>Tracked companies</b>
              <button onClick={() => onView("companies")}>View sources →</button>
            </header>
            {companies.slice(0, 5).map((company) => (
              <div key={company.id}>
                <i>{company.name.slice(0, 2).toUpperCase()}</i>
                <span>
                  <b>{company.name}</b>
                  <small>
                    {company.job_count} demo jobs ·{" "}
                    {company.recommended_job_count} recommended
                  </small>
                </span>
                <Badge
                  tone={
                    company.health_status === "HEALTHY"
                      ? "good"
                      : company.health_status === "UNKNOWN"
                        ? "neutral"
                        : "warn"
                  }
                >
                  {company.health_status}
                </Badge>
              </div>
            ))}
          </section>
          <section className="signal">
            <b>⚡ Source signal</b>
            <span>
              {sourceText} ·{" "}
              {runs[0]?.ended_at
                ? `last run ${formatDate(runs[0].ended_at)}`
                : "no completed run recorded"}
            </span>
          </section>
        </aside>
      </div>
    </>
  );
}
function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: number;
  detail: string;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <b>{value}</b>
      <small>{detail}</small>
    </div>
  );
}

function JobsPage({
  initialTab = "recommended",
  starredOnly = false,
  starredIds,
  onOpen,
  onStar,
}: {
  initialTab?: JobTab;
  starredOnly?: boolean;
  starredIds: string[];
  onOpen: (job: Job) => void;
  onStar: (job: Job) => void;
}) {
  const [search, setSearch] = useState("");
  const [company, setCompany] = useState("");
  const [location, setLocation] = useState("");
  const [technology, setTechnology] = useState("");
  const [decision, setDecision] = useState("");
  const [confidence, setConfidence] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<
    Record<string, { value: string; count: number }[]>
  >({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [agenticAvailable, setAgenticAvailable] = useState(false);
  const [agentic, setAgentic] = useState<AgenticSearchResponse | null>(null);
  useEffect(() => {
    let active = true;
    setLoading(true);
    dashboardApi
      .searchJobs({
        q: search || undefined,
        company: company || undefined,
        location: location || undefined,
        technology: technology || undefined,
        decision: decision || undefined,
        confidence: confidence || undefined,
      })
      .then((page) => {
        if (active) {
          const localItems = page.items.map((item) => ({ ...item, starred: starredIds.includes(item.id) }));
          setJobs(
            starredOnly
              ? localItems.filter((item) => item.starred)
              : localItems,
          );
          setTotal(
            starredOnly
              ? localItems.filter((item) => item.starred).length
              : page.total,
          );
          setFacets(page.facets);
        }
      })
      .catch((error) => active && setError(error.message))
      .finally(() => active && setLoading(false));
    dashboardApi
      .agenticSearchAvailable()
      .then((status) => active && setAgenticAvailable(status.enabled))
      .catch(() => active && setAgenticAvailable(false));
    return () => {
      active = false;
    };
  }, [
    search,
    company,
    location,
    technology,
    decision,
    confidence,
    starredOnly,
    starredIds,
  ]);
  const clear = () => {
    setSearch("");
    setCompany("");
    setLocation("");
    setTechnology("");
    setDecision("");
    setConfidence("");
    setAgentic(null);
  };
  const tryAgentic = async () => {
    try {
      setLoading(true);
      setError("");
      const response = await dashboardApi.agenticSearch(search);
      setAgentic(response);
      setJobs(response.items);
      setTotal(response.items.length);
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "AI-assisted search is unavailable.",
      );
    } finally {
      setLoading(false);
    }
  };
  const active = [
    search,
    company,
    location,
    technology,
    decision,
    confidence,
  ].filter(Boolean).length;
  const options = (key: string) => facets[key] ?? [];
  return (
    <>
      <PageTitle
        eyebrow={starredOnly ? "SHORTLIST" : "JOBS"}
        title={starredOnly ? "Starred jobs" : "Jobs"}
        detail={
          starredOnly
            ? "Your saved roles, across all states."
            : "Search persisted demo jobs and filter with backend facet counts."
        }
      />
      <section className="filters">
        <input
          value={search}
          placeholder="Search title, company, or description…"
          onChange={(event) => setSearch(event.target.value)}
        />
        <select
          value={company}
          onChange={(event) => setCompany(event.target.value)}
        >
          <option value="">All companies</option>
          {options("companies").map((item) => (
            <option key={item.value} value={item.value}>
              {item.value} ({item.count})
            </option>
          ))}
        </select>
        {(
          [
            ["locations", location, setLocation, "All locations"],
            ["technologies", technology, setTechnology, "All technologies"],
            ["decisions", decision, setDecision, "All decisions"],
            ["confidence", confidence, setConfidence, "All confidence"],
          ] as const
        ).map(([key, value, setValue, label]) => (
          <select
            key={key}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          >
            <option value="">{label}</option>
            {options(key).map((item) => (
              <option key={item.value} value={item.value}>
                {item.value} ({item.count})
              </option>
            ))}
          </select>
        ))}
        {active > 0 && (
          <button onClick={clear}>
            Clear {active} filter{active === 1 ? "" : "s"}
          </button>
        )}
      </section>
      {loading ? (
        <EmptyState title="Loading jobs">
          Reading the current server state.
        </EmptyState>
      ) : error ? (
        <div className="error">{error}</div>
      ) : (
        <>
          <p className="result-count">
            {total} role{total === 1 ? "" : "s"}
            {active
              ? ` · ${active} active filter${active === 1 ? "" : "s"}`
              : ""}
          </p>
          <div className="job-list">
            {jobs.map((job) => (
              <JobRow
                key={job.id}
                job={job}
                onOpen={() => onOpen(job)}
                onStar={() => onStar(job)}
              />
            ))}
            {!jobs.length && (
              <EmptyState title="No jobs found">
                Try clearing filters or use another search term.
                {search && agenticAvailable && (
                  <p>
                    <button onClick={tryAgentic}>Try AI-assisted search</button>
                  </p>
                )}
              </EmptyState>
            )}
            {agentic && (
              <div className="result-count">
                <b>AI-assisted search</b> · {agentic.original_query}
                {agentic.refined_query ? ` → ${agentic.refined_query}` : ""}
                {agentic.explanation ? ` · ${agentic.explanation}` : ""}
              </div>
            )}
          </div>
        </>
      )}
    </>
  );
}

function CompaniesPage({
  companies,
}: {
  companies: Company[];
}) {
  return (<><PageTitle eyebrow="DEMO SOURCES" title="Demo sources" detail="Source management is read-only in the public demo. Live discovery and source changes are disabled." /><section className="panel demo-note"><b>Public demo</b><span>No external job sources are contacted or changed.</span></section><div className="companies-grid">{companies.map((company) => <section className="panel company-card" key={company.id}><header><div><b>{company.name}</b><small>{company.domain} · {company.source_count} synthetic source{company.source_count === 1 ? "" : "s"}</small></div><Badge tone={company.health_status === "HEALTHY" ? "good" : "neutral"}>{company.health_status}</Badge></header><div className="company-stats"><span><b>{company.job_count}</b> synthetic jobs</span><span>External discovery disabled in public demo</span></div>{company.sources.map((source) => <div className="source-item" key={source.id}><div><b>{source.display_name}</b><small>Synthetic demo listing</small></div><div className="badges"><Badge tone="good">{source.lifecycle_status}</Badge><Badge tone="good">{source.readiness_status}</Badge></div></div>)}</section>)}</div></>);
  /*
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  async function add(event: FormEvent) {
    event.preventDefault();
    try {
      const parsed = new URL(url);
      const domain = parsed.hostname;
      const company = await dashboardApi.createCompany({
        name,
        domain,
        country: "Denmark",
      });
      const source = await dashboardApi.createSource(company.id, {
        source_id: `${domain.replace(/[^a-z0-9]+/gi, "_")}_preview`,
        display_name: name,
        official_jobs_url: url,
        canonical_url: url,
        allowed_domains: [domain],
        robots_txt_url: `https://${domain}/robots.txt`,
        crawl_delay_seconds: 5,
      });
      await dashboardApi.onboardSource(source.id, url);
      setName("");
      setUrl("");
      setMessage(
        "Company added. Its careers source is now being inspected for a safe preview.",
      );
      refresh();
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Could not add company.",
      );
    }
  }
  async function editSource(sourceId: string) {
    try {
      await dashboardApi.updateSource(sourceId, {
        official_jobs_url: sourceUrl,
        canonical_url: sourceUrl,
      });
      setEditing(null);
      setMessage(
        "Source URL updated. It now requires revalidation before it can scan again.",
      );
      refresh();
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Could not update source.",
      );
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="SOURCE CONTROL"
        title="Companies you follow"
        detail="Each company appears once; its career sources retain their own safe lifecycle."
        action={
          <button className="primary" onClick={onRun}>
            Run all scans
          </button>
        }
      />
      <section className="panel add">
        <b>Add a company</b>
        <small>
          Enter a company name and its careers page. The source stays in a safe
          preview flow until approved.
        </small>
        <form onSubmit={add}>
          <input
            required
            value={name}
            placeholder="Company name"
            onChange={(event) => setName(event.target.value)}
          />
          <input
            required
            type="url"
            value={url}
            placeholder="https://company.com/careers"
            onChange={(event) => setUrl(event.target.value)}
          />
          <button>Add company</button>
        </form>
        {message && <p>{message}</p>}
      </section>
      <div className="companies-grid">
        {companies.map((company) => (
          <section className="panel company-card" key={company.id}>
            <header>
              <div>
                <b>{company.name}</b>
                <small>
                  {company.domain} · {company.source_count} source
                  {company.source_count === 1 ? "" : "s"}
                </small>
              </div>
              <div className="actions">
                <Badge
                  tone={
                    company.health_status === "HEALTHY"
                      ? "good"
                      : company.health_status === "UNKNOWN"
                        ? "neutral"
                        : "warn"
                  }
                >
                  {company.health_status}
                </Badge>
                {company.lifecycle_status !== "ARCHIVED" && (
                  <button
                    className="danger-button"
                    onClick={() => {
                      if (
                        window.confirm(
                          `Archive ${company.name}? Its sources and job history will be retained.`,
                        )
                      )
                        dashboardApi
                          .archiveCompany(company.id)
                          .then(() => {
                            setMessage(
                              "Company archived; its job and source history was retained.",
                            );
                            refresh();
                          })
                          .catch((error) => setMessage(error.message));
                    }}
                  >
                    Archive
                  </button>
                )}
              </div>
            </header>
            <div className="company-stats">
              <span>
                <b>{company.unseen_job_count}</b> unseen
              </span>
              <span>
                <b>{company.recommended_job_count}</b> recommended
              </span>
              <span>
                <b>{company.job_count}</b> jobs
              </span>
              <span>
                {company.last_successful_run
                  ? `Last success ${formatDate(company.last_successful_run)}`
                  : "No successful scan yet"}
              </span>
            </div>
            {company.sources.map((source) => (
              <div className="source-item" key={source.id}>
                <div>
                  <b>{source.display_name}</b>
                  <small>{source.canonical_url ?? "No canonical URL"}</small>
                </div>
                <div className="badges">
                  <Badge
                    tone={
                      source.lifecycle_status === "ACTIVE"
                        ? "good"
                        : source.lifecycle_status === "ARCHIVED"
                          ? "danger"
                          : "warn"
                    }
                  >
                    {sourceLabels[source.lifecycle_status] ??
                      source.lifecycle_status}
                  </Badge>
                  <Badge>
                    {sourceLabels[source.readiness_status] ??
                      source.readiness_status}
                  </Badge>
                </div>
                <div className="source-actions">
                  <button
                    onClick={() => {
                      setEditing(source.id);
                      setSourceUrl(source.canonical_url ?? "");
                    }}
                  >
                    Edit URL
                  </button>
                  {source.lifecycle_status !== "ARCHIVED" && (
                    <button
                      onClick={() =>
                        dashboardApi
                          .revalidateSource(source.id)
                          .then(() => {
                            setMessage("Revalidation preview requested.");
                            refresh();
                          })
                          .catch((error) => setMessage(error.message))
                      }
                    >
                      Revalidate
                    </button>
                  )}
                  {source.lifecycle_status !== "ARCHIVED" && (
                    <button
                      className="danger-button"
                      onClick={() =>
                        dashboardApi
                          .archiveSource(source.id)
                          .then(() => {
                            setMessage(
                              "Source archived; job history was kept.",
                            );
                            refresh();
                          })
                          .catch((error) => setMessage(error.message))
                      }
                    >
                      Archive
                    </button>
                  )}
                </div>
                {editing === source.id && (
                  <form
                    className="source-edit"
                    onSubmit={(event) => {
                      event.preventDefault();
                      editSource(source.id);
                    }}
                  >
                    <input
                      type="url"
                      value={sourceUrl}
                      onChange={(event) => setSourceUrl(event.target.value)}
                      required
                    />
                    <button>Save & revalidate</button>
                    <button type="button" onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                  </form>
                )}
              </div>
            ))}
          </section>
        ))}
        {!companies.length && (
          <EmptyState title="No companies yet">
            Add a careers URL to create a safe inspection preview.
          </EmptyState>
        )}
      </div>
    </>
  );*/
}

function ProfilePage({
  profile,
}: {
  profile: Profile;
}) {
  const demoPreferences = {"Target roles": ["Backend Developer", "AI Product Developer", "Full-stack Developer"], "Locations": ["Denmark"], "Work modes": ["Hybrid", "Remote"], "Job languages": ["Danish", "English"]};
  return (<><PageTitle eyebrow="SYNTHETIC PROFILE" title="Demo candidate profile" detail="Synthetic demo profile — no private candidate data is used." /><div className="profile-grid"><section className="panel demo-profile"><h3>Demo Candidate <Badge>DEMO IDENTITY</Badge></h3><p>Example software developer profile · synthetic location</p><p>Example portfolio profile used solely to ground the synthetic job-match demonstration.</p><h4>Evidence-backed skills</h4><div className="fact-list">{(profile.facts?.items ?? []).slice(0, 8).map((item) => <span key={item.evidence_id}><b>{item.claim}</b><small>{displayEnum(item.classification)}</small></span>)}</div></section><section className="panel demo-profile"><h3>Demo preferences</h3>{Object.entries(demoPreferences).map(([label, values]) => <p key={label}><b>{label}:</b> {values.join(", ")}</p>)}<p><b>Minimum fit:</b> {(profile.minimum_fit / 10).toFixed(1)}/10</p></section></div></>);
  /*
  const [preferences, setPreferences] = useState<ProfilePreferences>(
    profile.preferences ?? {
      target_roles: profile.target_roles,
      locations: profile.locations,
      work_modes: profile.work_modes,
      job_languages: [],
      cover_letter_tone: profile.cover_letter_tone,
      minimum_fit: profile.minimum_fit,
    },
  );
  const [addition, setAddition] = useState("");
  const [message, setMessage] = useState("");
  useEffect(
    () =>
      setPreferences(
        profile.preferences ?? {
          target_roles: profile.target_roles,
          locations: profile.locations,
          work_modes: profile.work_modes,
          job_languages: [],
          cover_letter_tone: profile.cover_letter_tone,
          minimum_fit: profile.minimum_fit,
        },
      ),
    [profile],
  );
  const csv = (
    key: "target_roles" | "locations" | "work_modes" | "job_languages",
    value: string,
  ) =>
    setPreferences((current) => ({
      ...current,
      [key]: value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    }));
  async function save() {
    try {
      const updated = await dashboardApi.saveProfile({
        facts: addition.trim()
          ? { confirmed_additions: [addition.trim()] }
          : undefined,
        preferences,
      });
      onSave(updated);
      setAddition("");
      setMessage(
        updated.recalculated_matches
          ? `Profile updated. ${updated.recalculated_matches} matches recalculated; job analyses were reused.`
          : "Preferences saved. Existing matches were not recalculated.",
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Could not save profile.",
      );
    }
  }
  const visibleFacts = profile.facts?.items ?? [];
  return (
    <>
      <PageTitle
        eyebrow="MY PROFILE"
        title="Candidate profile & preferences"
        detail="Facts remain grounded in the canonical candidate profile; preferences affect visibility and presentation."
        action={
          <button className="primary" onClick={save}>
            Save changes
          </button>
        }
      />
      <div className="profile-grid">
        <section className="panel form">
          <header>
            <b>Candidate profile</b>
            <small>
              Canonical facts used for matching and grounded letters
            </small>
          </header>
          <label>
            Professional title
            <input value={profile.headline} readOnly />
          </label>
          <label>
            Summary
            <textarea value={profile.summary} readOnly />
          </label>
          <label>
            Languages
            <input value={profile.languages.join(", ")} readOnly />
          </label>
          <label>Skills & experience evidence</label>
          <div className="fact-list">
            {visibleFacts.slice(0, 14).map((item) => (
              <span key={item.evidence_id}>
                <b>{item.claim}</b>
                <small>{displayEnum(item.classification)}</small>
              </span>
            )) || <small>No canonical facts available.</small>}
          </div>
          <label>
            Add a confirmed fact{" "}
            <small>
              This is recorded as developing evidence, not direct experience.
            </small>
            <textarea
              value={addition}
              placeholder="One concise, factual professional update"
              onChange={(event) => setAddition(event.target.value)}
            />
          </label>
        </section>
        <section className="panel form">
          <header>
            <b>Preferences</b>
            <small>These do not become candidate evidence.</small>
          </header>
          <label>
            Target roles
            <input
              value={preferences.target_roles.join(", ")}
              onChange={(event) => csv("target_roles", event.target.value)}
            />
          </label>
          <label>
            Locations
            <input
              value={preferences.locations.join(", ")}
              onChange={(event) => csv("locations", event.target.value)}
            />
          </label>
          <label>
            Work modes
            <input
              value={preferences.work_modes.join(", ")}
              onChange={(event) => csv("work_modes", event.target.value)}
            />
          </label>
          <label>
            Job ad languages
            <input
              value={preferences.job_languages.join(", ")}
              onChange={(event) => csv("job_languages", event.target.value)}
            />
          </label>
          <label>
            Cover letter tone
            <select
              value={preferences.cover_letter_tone}
              onChange={(event) =>
                setPreferences((current) => ({
                  ...current,
                  cover_letter_tone: event.target.value,
                }))
              }
            >
              <option>Direct & professional</option>
              <option>Warm & conversational</option>
              <option>Concise & technical</option>
            </select>
          </label>
          <section className="threshold">
            <div>
              <b>Minimum fit score</b>
              <span>{(preferences.minimum_fit / 10).toFixed(1)}/10</span>
            </div>
            <input
              type="range"
              min="0"
              max="100"
              step="5"
              value={preferences.minimum_fit}
              onChange={(event) =>
                setPreferences((current) => ({
                  ...current,
                  minimum_fit: Number(event.target.value),
                }))
              }
            />
            <small>
              Changing this updates Recommended immediately; it does not
              re-analyze jobs.
            </small>
          </section>
        </section>
      </div>
      {message && <div className="toast">{message}</div>}
    </>
  );*/
}

function Drawer({
  job,
  history,
  onClose,
  onStar,
}: {
  job: Job;
  history: Match | null;
  onClose: () => void;
  onStar: () => void;
}) {
  const analyzed =
    job.analysis_status === "COMPLETED" && job.latest_match !== null;
  const requirements = Array.isArray(history?.evidence?.requirements)
    ? (history!.evidence!.requirements as Array<Record<string, unknown>>)
    : [];
  const decision = history?.evidence?.decision as { reasons?: string[] } | undefined;
  const demoReasons = decision?.reasons?.filter((reason) => !reason.toLowerCase().includes("start an application")) ?? [];
  const trace = history?.evidence && typeof history.evidence === "object"
    ? { ...history.evidence, decision: { ...(history.evidence.decision as object), next_step: "Action execution is disabled in the public read-only demo." } }
    : job.latest_match;
  return (
    <>
      <button
        className="scrim"
        aria-label="Close job details"
        onClick={onClose}
      />
      <aside className="drawer">
        <header>
          <div>
            <Badge>{job.language ?? "Language unknown"}</Badge>
            <h2>{job.title}</h2>
            <span>
              {job.company_name ?? job.source}
              {job.location_raw ? ` · ${job.location_raw}` : ""}
            </span>
          </div>
          <button
            className={`star ${job.starred ? "on" : ""}`}
            onClick={onStar}
          >
            {job.starred ? "★" : "☆"}
          </button>
          <button className="close" onClick={onClose}>
            ×
          </button>
        </header>
        <main>
          {!analyzed ? (
            <section className="not-evaluated">
              <h3>Not evaluated</h3>
              <p>
                This stored job has not completed analysis, so no fit score or
                cover-letter action is available.
              </p>
              <Badge tone="warn">Not analyzed</Badge>
            </section>
          ) : (
            <>
              <section className="fit">
                <FitScore job={job} />
                <div>
                  <h3>
                    {job.recommendation && displayEnum(job.recommendation)}
                  </h3>
                  <p>{demoReasons.join(" ") || "This recommendation is based on the latest evidence-backed match."}</p>
                  <p className="demo-listing">Review the supporting evidence and requirements below.</p>
                </div>
              </section>
              <section>
                <h4>Why it fits</h4>
                {job.matched_requirements.length ? (
                  job.matched_requirements.map((item) => (
                    <p key={item}>✓ {item}</p>
                  ))
                ) : (
                  <p>No direct requirement matches were recorded.</p>
                )}
              </section>
              <section>
                <h4>Important gaps</h4>
                {job.gaps.length ? (
                  <div className="badges">
                    {job.gaps.map((item) => (
                      <Badge key={item} tone="warn">
                        {item}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p>No major gaps were recorded.</p>
                )}
              </section>
              <section>
                <h4>Requirements</h4>
                <div className="requirement-grid">
                  {requirements.map((item, index) => (
                    <span key={index} className={String(item.classification)}>
                      {String(item.requirement)}
                    </span>
                  )) || <span>Compact match details are shown above.</span>}
                </div>
              </section>
            </>
          )}
          <section>
            <h4>Job summary</h4>
            <p>
              {job.location_raw ?? "Location not listed"} ·{" "}
              {job.language ?? "Language unknown"} ·{" "}
              {mode(job) ?? "Work mode not listed"} · {recency(job)}
            </p>
          </section>
          <section>
            <h4>Full description</h4>
            <p className="description">
              {job.description ?? "No description was stored for this job."}
            </p>
          </section>
          <details>
            <summary>Technical match trace</summary>
            <pre>
              {JSON.stringify(trace, null, 2)}
            </pre>
          </details>
          <p className="demo-listing">Synthetic demo listing · Application tracking is disabled in the public read-only demo.</p>
        </main>
      </aside>
    </>
  );
}

function App() {
  const [view, setView] = useState<View>("dashboard");
  const [profile, setProfile] = useState<Profile>(blank);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [sources, setSources] = useState<SourceHealth[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [dash, setDash] = useState<Record<JobTab, Job[]>>({
    recommended: [],
    unseen: [],
    applied: [],
    all: [],
  });
  const [starredIds, setStarredIds] = useState<string[]>(() => JSON.parse(localStorage.getItem("job-fit-demo-stars") ?? "[]"));
  const [selected, setSelected] = useState<Job | null>(null);
  const [history, setHistory] = useState<Match | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const refresh = async () => {
    setLoading(true);
    try {
      const [
        recommended,
        unseen,
        applied,
        all,
        companyRows,
        sourceRows,
        runRows,
        candidate,
      ] = await Promise.all([
        dashboardApi.jobs({
          view: "recommended",
          page_size: 100,
          sort: "best_fit",
        }),
        dashboardApi.jobs({
          view: "unseen",
          page_size: 100,
          sort: "newest_discovered",
        }),
        dashboardApi.jobs({
          view: "applied",
          page_size: 100,
          sort: "newest_discovered",
        }),
        dashboardApi.jobs({
          view: "all",
          page_size: 100,
          sort: "newest_discovered",
        }),
        dashboardApi.companies(),
        dashboardApi.sources(),
        dashboardApi.runs(),
        dashboardApi.profile(),
      ]);
      const local = (items: Job[]) => items.map((item) => ({ ...item, starred: starredIds.includes(item.id) }));
      setDash({ recommended: local(recommended.items), unseen: local(unseen.items), applied: local(applied.items), all: local(all.items) });
      setCompanies(companyRows);
      setSources(sourceRows);
      setRuns(runRows);
      setProfile(candidate);
      setError("");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load the workspace.",
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    refresh();
  }, []);
  async function open(job: Job) {
    setSelected(job);
    try {
      const result = await dashboardApi.history(job.id);
      setHistory(result.matches[0] ?? null);
    } catch {
      setHistory(null);
    }
  }
  function star(job: Job) {
    const nowStarred = !starredIds.includes(job.id);
    const nextIds = nowStarred ? [...starredIds, job.id] : starredIds.filter((id) => id !== job.id);
    localStorage.setItem("job-fit-demo-stars", JSON.stringify(nextIds));
    setStarredIds(nextIds);
    const updated = { ...job, starred: nowStarred };
    setDash(
      (current) =>
        Object.fromEntries(
          Object.entries(current).map(([key, rows]) => [
            key,
            rows.map((item) => (item.id === updated.id ? updated : item)),
          ]),
        ) as Record<JobTab, Job[]>,
    );
    if (selected?.id === updated.id) setSelected(updated);
  }
  const starred = dash.all.filter((job) => job.starred);
  return (
    <div className="shell">
      <Sidebar
        view={view}
        setView={setView}
        counts={{ jobs: dash.all.length, starred: starred.length }}
      />
      <div className="workspace">
        <header className="top">
          <span>
            <small>
              {new Date()
                .toLocaleDateString("en-GB", {
                  weekday: "long",
                  day: "numeric",
                  month: "long",
                  year: "numeric",
                })
                .toUpperCase()}
            </small>
            <b>
              {
                {
                  dashboard: "Dashboard",
                  jobs: "Jobs",
                  starred: "Starred",
                  companies: "Companies",
                  profile: "My Profile",
                }[view]
              }
            </b>
          </span>
          <span
            className={`health ${sources.some((item) => item.health_status === "UNHEALTHY") ? "bad" : ""}`}
          >
            ●{" "}
            {sources.length
              ? `Sources: ${sources.filter((item) => item.health_status === "HEALTHY").length}/${sources.length} healthy`
              : "Health unknown"}
            <i>DEMO</i>
          </span>
        </header>
        <main className="content">
          {loading ? (
            <EmptyState title="Preparing your workspace">
              Reading the current local server state.
            </EmptyState>
          ) : error ? (
            <div className="error">{error}</div>
          ) : view === "dashboard" ? (
            <Dashboard
              recommended={dash.recommended}
              jobs={dash.all}
              starred={starred}
              companies={companies}
              sources={sources}
              runs={runs}
              onOpen={open}
              onStar={star}
              onView={setView}
            />
          ) : view === "jobs" ? (
            <JobsPage onOpen={open} onStar={star} starredIds={starredIds} />
          ) : view === "starred" ? (
            <JobsPage starredOnly onOpen={open} onStar={star} starredIds={starredIds} />
          ) : view === "companies" ? (
            <CompaniesPage companies={companies} />
          ) : (
            <ProfilePage profile={profile} />
          )}
        </main>
      </div>
      {selected && (
        <Drawer
          job={selected}
          history={history}
          onClose={() => setSelected(null)}
          onStar={() => star(selected)}
        />
      )}
    </div>
  );
}

export { App };

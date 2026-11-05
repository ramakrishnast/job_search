// Typed API client for the JobPilot backend.

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? data);
    throw new ApiError(res.status, detail || res.statusText, data?.code);
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T>(p: string, b: unknown) => request<T>("PUT", p, b),
  patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  });
  const s = u.toString();
  return s ? `?${s}` : "";
}

// ------------------------------------------------------------------ types

export type Verdict = "Strong" | "Stretch" | "Skip";

export interface Fit {
  score: number;
  verdict: Verdict;
  method: "llm" | "heuristic";
  model: string | null;
  strengths: string[];
  gaps: string[];
  verdict_line: string;
  parsed: JDParse | null;
  created_at: string;
  stale: boolean;
}

export interface JDParse {
  title_normalized: string;
  seniority: string;
  required_skills: string[];
  nice_to_haves: string[];
  years_experience_min: number | null;
  location: string | null;
  remote_policy: string;
  comp_min: number | null;
  comp_max: number | null;
  comp_currency: string | null;
  comp_raw: string | null;
  responsibilities: string[];
  role_summary: string;
}

export interface Application {
  id: number;
  job_id: number;
  stage: string;
  position: number;
  notes: string;
  next_action: string | null;
  next_action_date: string | null;
  follow_up_at: string | null;
  applied_at: string | null;
  updated_at?: string;
  events?: { from: string | null; to: string; at: string }[];
  contacts?: Contact[];
}

export interface Contact {
  id: number;
  name: string;
  role: string | null;
  email: string | null;
  linkedin_url: string | null;
  notes: string;
}

export interface JobSummary {
  id: number;
  company: string;
  company_id: number;
  source: string;
  title: string;
  location: string | null;
  locations: string[];
  location_tags: string[];
  is_remote: boolean;
  workplace_type: string | null;
  seniority: string | null;
  url: string | null;
  apply_url: string | null;
  comp_text: string | null;
  posted_at: string | null;
  first_seen_at: string;
  closed_at: string | null;
  matches_filter: boolean;
  filter_reasons: string[];
  archived: boolean;
  fit: Fit | null;
  stage: string | null;
  application_id: number | null;
  application?: Application;
}

export interface Tailoring {
  kind: "resume" | "kit";
  data: any;
  warnings: string[];
  model: string;
  created_at: string;
  stale: boolean;
}

export interface JobDetail extends JobSummary {
  description_text: string;
  departments: string[];
  parse: JDParse | null;
  application: Application | undefined;
  tailoring: { resume: Tailoring | null; kit: Tailoring | null };
}

export interface Status {
  version: string;
  llm: { configured: boolean; provider: string; model_parse: string; model_default: string; model_interview: string; daily_budget_usd: number; spent_24h_usd: number };
  discovery: { running: boolean; schedule_enabled: boolean; next_run: string | null; schedule: string; last_run: DiscoveryRun | null };
  counts: Record<string, number>;
  data_dir: string;
}

export interface DiscoveryRun {
  id: number;
  trigger: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  fetched_count: number;
  new_count: number;
  new_matching_count: number;
  closed_count: number;
  scored_count: number;
  company_stats: { company: string; ats: string; status: string; error: string | null; fetched?: number; new?: number; new_matching?: number; closed?: number; matching_open?: number }[];
  error: string | null;
}

export interface Company {
  id: number;
  name: string;
  ats: string;
  slug: string | null;
  careers_url: string | null;
  category: string | null;
  enabled: boolean;
  source: string;
  last_fetch_at: string | null;
  last_status: string | null;
  last_error: string | null;
  last_job_count: number | null;
}

export interface Question {
  id: number;
  key: string;
  category: string;
  topic: string;
  difficulty: number;
  prompt: string;
  rubric_hints: string[];
  follow_up_hints: string[];
  source: string;
  job_id: number | null;
  active: boolean;
  function_name: string | null;
  starter_code: string | null;
  has_tests: boolean;
  review: { due_at: string; last_score: number | null; attempts: number; interval_days: number; ease: number; lapses: number } | null;
}

export interface Turn {
  id: number;
  seq: number;
  role: "interviewer" | "candidate";
  kind: string;
  question_index: number;
  question_id: number | null;
  content: string;
  code: string | null;
  code_result: CodeResult | null;
  whiteboard_image: string | null;
  time_taken_s: number | null;
  input_mode: string | null;
}

export interface CodeResult {
  ok: boolean;
  passed: number;
  total: number;
  results: { name: string; passed: boolean; error?: string; stdout?: string }[];
  error: string | null;
  stdout?: string;
  duration_ms: number;
}

export interface Scorecard {
  overall_score: number;
  hire_signal: string;
  summary: string;
  rubric: Record<"technical_depth" | "communication" | "structure" | "tradeoffs", { score: number; justification: string }>;
  strengths: string[];
  gaps: string[];
  per_question: { question_index: number; score: number; went_well: string[]; to_improve: string[]; model_answer: string }[];
  topics_to_review: string[];
}

export interface Session {
  id: number;
  kind: string;
  job_id: number | null;
  status: "active" | "completed" | "abandoned";
  difficulty: number | null;
  config: { num_questions: number; timer_seconds: number | null; max_followups: number };
  current_index: number;
  num_questions: number;
  started_at: string;
  ended_at: string | null;
  overall_score: number | null;
  rubric: Record<string, number | null>;
  turns?: Turn[];
  scorecard?: Scorecard | null;
  questions?: Question[];
}

export interface Story {
  id: number;
  title: string;
  project: Record<string, string>;
  situation: string;
  task: string;
  action: string;
  result: string;
  competencies: string[];
  question_ids: string[];
  method: string;
  updated_at: string;
}

export interface Notification {
  id: number;
  kind: string;
  title: string;
  body: string;
  link: string | null;
  created_at: string;
  read: boolean;
}

export const STAGE_LABELS: Record<string, string> = {
  discovered: "Discovered",
  shortlisted: "Shortlisted",
  applied: "Applied",
  recruiter_screen: "Recruiter Screen",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export const KIND_LABELS: Record<string, string> = {
  system_design: "LLM System Design",
  ml_fundamentals: "ML Fundamentals",
  behavioral: "Behavioral",
  coding: "Coding",
  company: "Company Loop",
};

/** Backend timestamps are naive UTC; scheduler times carry an explicit offset. */
export function parseTs(s: string): Date {
  return new Date(/(Z|[+-]\d\d:?\d\d)$/.test(s) ? s : s + "Z");
}

export function fmtDate(s: string | null | undefined, withTime = false): string {
  if (!s) return "—";
  const d = parseTs(s);
  return withTime ? d.toLocaleString() : d.toLocaleDateString();
}

export function relDays(s: string | null | undefined): string {
  if (!s) return "";
  const d = parseTs(s);
  const days = Math.round((Date.now() - d.getTime()) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  return `${days}d ago`;
}

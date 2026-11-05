import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Plus, RefreshCw, Sparkles } from "lucide-react";
import { api, qs, relDays, STAGE_LABELS, type JobSummary } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Field, inputCls, Modal, Spinner, VerdictBadge } from "../components/ui";

const TAGS: Record<string, string> = { bay_area: "Bay Area", remote_us: "Remote US", atlanta: "Atlanta", nyc: "NYC" };

function AddJobModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ company: "", title: "", url: "", location: "", description: "", is_remote: false });
  const m = useMutation({
    mutationFn: () => api.post<JobSummary>("/jobs", f),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["pipeline"] });
      onClose();
    },
  });
  return (
    <Modal open={open} onClose={onClose} title="Add a job manually" wide>
      <p className="mb-3 text-xs text-slate-500">For roles without a public ATS feed (e.g. big-tech career sites): paste the JD to score and tailor it.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Company"><input className={inputCls} value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} /></Field>
        <Field label="Title"><input className={inputCls} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Official posting URL"><input className={inputCls} value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
        <Field label="Location"><input className={inputCls} value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} /></Field>
      </div>
      <div className="mt-3">
        <Field label="Job description"><textarea className={inputCls + " h-48"} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      </div>
      <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={f.is_remote} onChange={(e) => setF({ ...f, is_remote: e.target.checked })} /> Remote</label>
      <ErrorBox error={m.error} />
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" loading={m.isPending} disabled={!f.company || !f.title || f.description.length < 20} onClick={() => m.mutate()}>Add job</Button>
      </div>
    </Modal>
  );
}

export default function Jobs() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const filters = {
    q: params.get("q") ?? "",
    matching: params.get("matching") !== "0",
    status: params.get("status") ?? "open",
    new_days: params.get("new") ? 7 : undefined,
    verdict: params.get("verdict") ?? "",
    location_tag: params.get("loc") ?? "",
    sort: params.get("sort") ?? "score",
  };
  const set = (k: string, v: string | null) => {
    const p = new URLSearchParams(params);
    if (v === null || v === "") p.delete(k);
    else p.set(k, v);
    setParams(p, { replace: true });
  };
  const { data, isLoading, error } = useQuery({
    queryKey: ["jobs", filters],
    queryFn: () => api.get<{ total: number; jobs: JobSummary[] }>("/jobs" + qs({ ...filters, limit: 300 })),
  });
  const batch = useMutation({
    mutationFn: (ids: number[]) => api.post<{ scored: unknown[]; errors: { job_id: number; error: string }[] }>("/jobs/score-batch", { job_ids: ids }),
    onSuccess: () => {
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  const unscored = data?.jobs.filter((j) => !j.fit).map((j) => j.id) ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Jobs {data && <span className="text-sm font-normal text-slate-500">({data.total})</span>}</h1>
        <div className="flex gap-2">
          <Button onClick={() => setAdding(true)}><Plus className="h-4 w-4" /> Add manually</Button>
          <Button
            variant="primary"
            loading={batch.isPending}
            disabled={!selected.size && !unscored.length}
            onClick={() => batch.mutate((selected.size ? [...selected] : unscored).slice(0, 50))}
          >
            <Sparkles className="h-4 w-4" /> Score {selected.size ? `${selected.size} selected` : `${Math.min(unscored.length, 50)} unscored`}
          </Button>
        </div>
      </div>
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Search"><input className={inputCls + " w-56"} placeholder="title, company, location" defaultValue={filters.q} onKeyDown={(e) => e.key === "Enter" && set("q", (e.target as HTMLInputElement).value)} onBlur={(e) => set("q", e.target.value)} /></Field>
          <Field label="Status">
            <select className={inputCls} value={filters.status} onChange={(e) => set("status", e.target.value)}>
              <option value="open">Open</option><option value="closed">Closed</option><option value="all">All</option>
            </select>
          </Field>
          <Field label="Verdict">
            <select className={inputCls} value={filters.verdict} onChange={(e) => set("verdict", e.target.value)}>
              <option value="">Any</option><option>Strong</option><option>Stretch</option><option>Skip</option>
            </select>
          </Field>
          <Field label="Location">
            <select className={inputCls} value={filters.location_tag} onChange={(e) => set("loc", e.target.value)}>
              <option value="">Any target</option>
              {Object.entries(TAGS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          <Field label="Sort">
            <select className={inputCls} value={filters.sort} onChange={(e) => set("sort", e.target.value)}>
              <option value="score">Fit score</option><option value="newest">Newest</option><option value="company">Company</option>
            </select>
          </Field>
          <label className="flex items-center gap-1.5 pb-2 text-sm"><input type="checkbox" checked={!!filters.new_days} onChange={(e) => set("new", e.target.checked ? "1" : null)} /> New (7d)</label>
          <label className="flex items-center gap-1.5 pb-2 text-sm"><input type="checkbox" checked={!filters.matching} onChange={(e) => set("matching", e.target.checked ? "0" : null)} /> Include non-matching</label>
        </div>
      </Card>
      {batch.data?.errors.length ? <ErrorBox error={`${batch.data.errors.length} job(s) failed to score: ${batch.data.errors[0].error}`} /> : null}
      <ErrorBox error={batch.error} />
      {isLoading ? <Spinner /> : error ? <ErrorBox error={error} /> : !data?.jobs.length ? (
        <Empty>
          No jobs yet. Run discovery from the <Link className="underline" to="/companies">Discovery</Link> page, or add one manually.
        </Empty>
      ) : (
        <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {data.jobs.map((j) => (
              <li key={j.id} className="flex items-start gap-3 px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-800/40">
                <input
                  type="checkbox"
                  aria-label={`select ${j.title}`}
                  className="mt-1"
                  checked={selected.has(j.id)}
                  onChange={(e) => {
                    const s = new Set(selected);
                    if (e.target.checked) s.add(j.id);
                    else s.delete(j.id);
                    setSelected(s);
                  }}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link to={`/jobs/${j.id}`} className="font-medium hover:underline">{j.title}</Link>
                    {relDays(j.first_seen_at) === "today" && <Badge tone="blue">new</Badge>}
                    {j.closed_at && <Badge tone="rose">closed</Badge>}
                    {!j.matches_filter && <Badge>non-matching</Badge>}
                    {j.stage && j.stage !== "discovered" && <Badge tone="violet">{STAGE_LABELS[j.stage]}</Badge>}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-slate-500">
                    <span className="font-medium text-slate-700 dark:text-slate-300">{j.company}</span>
                    <span className="flex items-center gap-1"><MapPin className="h-3 w-3" />{j.location || (j.is_remote ? "Remote" : "—")}</span>
                    {j.location_tags.map((t) => <span key={t}>{TAGS[t] ?? t}</span>)}
                    {j.comp_text && <span>{j.comp_text}</span>}
                    <span>seen {relDays(j.first_seen_at)}</span>
                  </div>
                  {j.fit?.verdict_line && <div className="mt-1 line-clamp-1 text-xs text-slate-600 dark:text-slate-400">{j.fit.verdict_line}</div>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {j.fit ? <VerdictBadge verdict={j.fit.verdict} score={j.fit.score} method={j.fit.method} /> : <Badge>unscored</Badge>}
                  {j.fit?.stale && <span title="Profile or JD changed since scoring"><RefreshCw className="h-3.5 w-3.5 text-amber-500" /></span>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      <AddJobModal open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}

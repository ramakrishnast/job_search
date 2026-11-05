import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Play, Plus, SearchCheck } from "lucide-react";
import { api, fmtDate, type Company, type DiscoveryRun, type Status } from "../api";
import { Badge, Button, Card, ErrorBox, Field, inputCls, Spinner } from "../components/ui";

export default function Companies() {
  const qc = useQueryClient();
  const { data: companies, isLoading } = useQuery({ queryKey: ["companies"], queryFn: () => api.get<Company[]>("/companies") });
  const { data: runs } = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.get<{ running: boolean; runs: DiscoveryRun[] }>("/discovery/runs?limit=10"),
    refetchInterval: (q) => (q.state.data?.running ? 3000 : 30000),
  });
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: () => api.get<Status>("/status") });
  const run = useMutation({
    mutationFn: () => api.post("/discovery/run"),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["runs"] }), 500),
  });
  const verify = useMutation({ mutationFn: () => api.post<{ company: string; configured: string; detected: string | null; jobs: number | null; ok: boolean }[]>("/companies/verify") });
  const toggle = useMutation({
    mutationFn: (c: Company) => api.patch(`/companies/${c.id}`, { enabled: !c.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["companies"] }),
  });
  const [nc, setNc] = useState({ name: "", slug: "", ats: "auto" });
  const add = useMutation({
    mutationFn: () => api.post("/companies", nc),
    onSuccess: () => { setNc({ name: "", slug: "", ats: "auto" }); qc.invalidateQueries({ queryKey: ["companies"] }); },
  });
  const refilter = useMutation({ mutationFn: () => api.post<{ newly_matching: number }>("/discovery/refilter") });
  const last = runs?.runs[0];
  const feeds = companies?.filter((c) => c.ats !== "manual") ?? [];
  const manual = companies?.filter((c) => c.ats === "manual") ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Discovery</h1>
        <div className="flex gap-2">
          <Button onClick={() => verify.mutate()} loading={verify.isPending}><SearchCheck className="h-4 w-4" /> Verify boards</Button>
          <Button onClick={() => refilter.mutate()} loading={refilter.isPending}>Re-apply filters</Button>
          <Button variant="primary" onClick={() => run.mutate()} loading={run.isPending || runs?.running}><Play className="h-4 w-4" /> {runs?.running ? "Running…" : "Run discovery now"}</Button>
        </div>
      </div>
      <p className="text-sm text-slate-500">
        Public Greenhouse, Lever and Ashby job-board APIs only (robots.txt respected, ~1 request/second per host). Scheduled {status?.discovery.schedule}
        {status?.discovery.next_run && <> — next run {fmtDate(status.discovery.next_run, true)}</>}.
      </p>
      {refilter.data && <div className="text-sm text-emerald-700">{refilter.data.newly_matching} job(s) newly match your filters.</div>}
      <ErrorBox error={run.error || verify.error || add.error} />

      {last && (
        <Card title={`Last run · ${fmtDate(last.started_at, true)} · ${last.trigger}`} actions={<Badge tone={last.status === "ok" ? "green" : last.status === "running" ? "blue" : last.status === "partial" ? "amber" : "rose"}>{last.status}</Badge>}>
          <div className="mb-3 flex flex-wrap gap-4 text-sm">
            <span>Fetched <b>{last.fetched_count}</b></span><span>New <b>{last.new_count}</b></span>
            <span>New matching <b>{last.new_matching_count}</b></span><span>Closed <b>{last.closed_count}</b></span><span>Auto-scored <b>{last.scored_count}</b></span>
          </div>
          {last.error && <ErrorBox error={last.error} />}
          <div className="max-h-80 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500"><tr><th className="py-1">Company</th><th>ATS</th><th>Status</th><th className="text-right">Fetched</th><th className="text-right">New</th><th className="text-right">Matching open</th></tr></thead>
              <tbody>
                {last.company_stats.map((s) => (
                  <tr key={s.company} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-1">{s.company}</td><td className="text-slate-500">{s.ats}</td>
                    <td>{s.status === "ok" ? <Badge tone="green">ok</Badge> : <span title={s.error ?? ""}><Badge tone="rose">{s.status}</Badge> <span className="text-xs text-slate-500">{s.error?.slice(0, 60)}</span></span>}</td>
                    <td className="text-right tabular-nums">{s.fetched ?? "—"}</td><td className="text-right tabular-nums">{s.new ?? "—"}</td><td className="text-right tabular-nums">{s.matching_open ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {verify.data && (
        <Card title="Board verification">
          <ul className="grid gap-1 text-sm sm:grid-cols-2">
            {verify.data.map((v) => (
              <li key={v.company} className="flex justify-between">
                <span>{v.company}</span>
                <span>{v.ok ? <Badge tone="green">{v.detected} · {v.jobs} jobs</Badge> : v.configured === "manual" ? <Badge>manual</Badge> : <Badge tone="rose">{v.detected ? `found on ${v.detected}` : "not found"}</Badge>}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-slate-500">Fix mismatches in <code>config/companies.yaml</code> (or <code>companies.local.yaml</code>).</p>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={`Feeds (${feeds.length})`} className="lg:col-span-2">
          {isLoading ? <Spinner /> : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-slate-500"><tr><th className="py-1">Company</th><th>ATS / slug</th><th>Last fetch</th><th className="text-right">Jobs</th><th /></tr></thead>
              <tbody>
                {feeds.map((c) => (
                  <tr key={c.id} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-1.5">{c.name} {c.source === "ui" && <Badge>custom</Badge>}</td>
                    <td className="text-slate-500">{c.ats} / {c.slug}</td>
                    <td className="text-xs">{c.last_status === "error" ? <span className="text-rose-600" title={c.last_error ?? ""}>error</span> : fmtDate(c.last_fetch_at, true)}</td>
                    <td className="text-right tabular-nums">{c.last_job_count ?? "—"}</td>
                    <td className="text-right"><label className="inline-flex items-center gap-1 text-xs"><input type="checkbox" checked={c.enabled} onChange={() => toggle.mutate(c)} /> on</label></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <div className="space-y-4">
          <Card title="Add a company">
            <div className="space-y-2">
              <Field label="Name"><input className={inputCls} value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} /></Field>
              <Field label="Board slug" hint="e.g. 'anthropic' from boards.greenhouse.io/anthropic"><input className={inputCls} value={nc.slug} onChange={(e) => setNc({ ...nc, slug: e.target.value })} /></Field>
              <Field label="ATS">
                <select className={inputCls} value={nc.ats} onChange={(e) => setNc({ ...nc, ats: e.target.value })}>
                  <option value="auto">Auto-detect</option><option value="greenhouse">Greenhouse</option><option value="lever">Lever</option><option value="ashby">Ashby</option>
                </select>
              </Field>
              <Button variant="primary" disabled={!nc.name || !nc.slug} loading={add.isPending} onClick={() => add.mutate()}><Plus className="h-4 w-4" /> Add</Button>
            </div>
          </Card>
          <Card title="Check manually (no public feed)">
            <ul className="space-y-1 text-sm">
              {manual.map((c) => (
                <li key={c.id}>
                  {c.careers_url ? <a className="inline-flex items-center gap-1 text-brand-600 hover:underline" href={c.careers_url} target="_blank" rel="noreferrer">{c.name} <ExternalLink className="h-3 w-3" /></a> : c.name}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-500">Found something? Use Jobs → Add manually to score and tailor it.</p>
          </Card>
        </div>
      </div>
    </div>
  );
}

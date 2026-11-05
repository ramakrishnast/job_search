import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, fmtDate, KIND_LABELS, STAGE_LABELS } from "../api";
import { Badge, Card, Empty, ErrorBox, Spinner, Stat } from "../components/ui";

interface Dash {
  pipeline: {
    stage_counts: Record<string, number>;
    applied_total: number;
    responded_total: number;
    response_rate: number | null;
    conversion: { from: string; to: string; from_count: number; to_count: number; rate: number | null }[];
    applications_per_week: { week: string; applications: number }[];
    upcoming: { application_id: number; job_id: number; title: string; company: string; stage: string; kind: string; label: string | null; due: string; overdue: boolean }[];
  };
  jobs: { open_matching: number; new_matching_7d: number; verdicts: Record<string, number> };
  interviews: {
    sessions: { id: number; date: string; kind: string; overall: number | null; technical_depth: number | null; communication: number | null; structure: number | null; tradeoffs: number | null }[];
    averages_by_kind: Record<string, number>;
    topics: { category: string; topic: string; avg_score: number | null; attempts: number; due: number }[];
    completed: number;
  };
  llm: { cost_30d: number; cost_24h: number; by_task_30d: { task: string; calls: number; cost_usd: number }[] };
}

const pct = (x: number | null) => (x === null ? "—" : `${Math.round(x * 100)}%`);
const KIND_COLORS: Record<string, string> = { system_design: "#2f6fa8", ml_fundamentals: "#0d9488", behavioral: "#a855f7", coding: "#f59e0b", company: "#e11d48" };

export default function Dashboard() {
  const { data, isLoading, error } = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dash>("/dashboard") });
  if (isLoading) return <Spinner />;
  if (error || !data) return <ErrorBox error={error} />;
  const p = data.pipeline;
  const weekly = p.applications_per_week.map((w) => ({ ...w, label: w.week.slice(5) }));
  const kinds = Array.from(new Set(data.interviews.sessions.map((s) => s.kind)));
  const series = data.interviews.sessions.map((s) => ({ date: fmtDate(s.date), [s.kind]: s.overall }));

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Dashboard</h1>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Open matching roles" value={data.jobs.open_matching} sub={`${data.jobs.new_matching_7d} new this week`} />
        <Stat label="Applied" value={p.applied_total} sub={`${p.stage_counts.interviewing ?? 0} interviewing`} />
        <Stat label="Response rate" value={pct(p.response_rate)} sub={`${p.responded_total} of ${p.applied_total} applications`} />
        <Stat label="Mock interviews" value={data.interviews.completed} sub={Object.entries(data.interviews.averages_by_kind).map(([k, v]) => `${KIND_LABELS[k] ?? k}: ${v}`).join(" · ") || "none yet"} />
        <Stat label="LLM spend" value={`$${data.llm.cost_30d.toFixed(2)}`} sub={`30 days · $${data.llm.cost_24h.toFixed(2)} last 24h`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Applications per week" className="lg:col-span-2">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={weekly}>
                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.3} />
                <XAxis dataKey="label" fontSize={11} />
                <YAxis allowDecimals={false} fontSize={11} width={28} />
                <Tooltip />
                <Bar dataKey="applications" fill="#2f6fa8" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Upcoming follow-ups">
          {!p.upcoming.length ? (
            <Empty>Nothing due in the next 14 days.</Empty>
          ) : (
            <ul className="space-y-2">
              {p.upcoming.slice(0, 8).map((u) => (
                <li key={`${u.application_id}-${u.kind}`} className="text-sm">
                  <Link to={`/jobs/${u.job_id}`} className="font-medium hover:underline">
                    {u.label || "Follow up"}
                  </Link>
                  <div className="text-xs text-slate-500">
                    {u.company} · {u.title}
                  </div>
                  <div className={u.overdue ? "text-xs font-medium text-rose-600" : "text-xs text-slate-500"}>
                    {u.overdue ? "Overdue · " : ""}
                    {fmtDate(u.due, true)}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Stage conversion">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="py-1">From → To</th>
                <th>Reached</th>
                <th className="text-right">Rate</th>
              </tr>
            </thead>
            <tbody>
              {p.conversion.map((c) => (
                <tr key={c.from} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-1.5">
                    {STAGE_LABELS[c.from]} → {STAGE_LABELS[c.to]}
                  </td>
                  <td className="tabular-nums text-slate-500">
                    {c.to_count}/{c.from_count}
                  </td>
                  <td className="text-right font-medium tabular-nums">{pct(c.rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {Object.entries(p.stage_counts).map(([s, n]) => (
              <Badge key={s}>
                {STAGE_LABELS[s]}: {n}
              </Badge>
            ))}
          </div>
        </Card>
        <Card title="Interview scores over time">
          {!series.length ? (
            <Empty>
              Complete a <Link className="underline" to="/interview">mock interview</Link> to see progress.
            </Empty>
          ) : (
            <div className="h-56">
              <ResponsiveContainer>
                <LineChart data={series}>
                  <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.3} />
                  <XAxis dataKey="date" fontSize={11} />
                  <YAxis domain={[0, 100]} fontSize={11} width={28} />
                  <Tooltip />
                  <Legend formatter={(v) => KIND_LABELS[v] ?? v} />
                  {kinds.map((k) => (
                    <Line key={k} type="monotone" dataKey={k} stroke={KIND_COLORS[k] ?? "#64748b"} connectNulls dot />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>

      <Card title="Weakest topics (spaced repetition)">
        {!data.interviews.topics.length ? (
          <Empty>Topic scores appear after your first scored interview.</Empty>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {data.interviews.topics.map((t) => (
              <div key={`${t.category}-${t.topic}`} className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-2 text-sm dark:bg-slate-800/50">
                <span>
                  {t.topic} <span className="text-xs text-slate-500">({KIND_LABELS[t.category] ?? t.category})</span>
                </span>
                <span className="flex items-center gap-2">
                  {t.due > 0 && <Badge tone="amber">{t.due} due</Badge>}
                  <span className="tabular-nums font-medium">{t.avg_score?.toFixed(1) ?? "—"}/10</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

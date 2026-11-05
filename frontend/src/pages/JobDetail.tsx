import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, ExternalLink, GraduationCap, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { api, fmtDate, STAGE_LABELS, type Application, type Contact, type JobDetail as JD, type Session } from "../api";
import { Badge, Button, Card, cx, Empty, ErrorBox, Field, inputCls, Spinner, VerdictBadge } from "../components/ui";

const TABS = ["Overview", "Tailored resume", "Application kit", "Tracking"] as const;

function CopyBtn({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button variant="ghost" onClick={() => navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1200); })}>
      <Copy className="h-3.5 w-3.5" /> {done ? "Copied" : "Copy"}
    </Button>
  );
}

function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  const off = d.getTimezoneOffset();
  return new Date(d.getTime() - off * 60000).toISOString().slice(0, 16);
}
function fromLocalInput(v: string): string | null {
  return v ? new Date(v).toISOString() : null;
}

function Tracking({ job }: { job: JD }) {
  const qc = useQueryClient();
  const app = job.application;
  const [form, setForm] = useState({ notes: "", next_action: "", next_action_date: "", follow_up_at: "" });
  const [contact, setContact] = useState({ name: "", role: "", email: "", linkedin_url: "" });
  useEffect(() => {
    if (app) setForm({ notes: app.notes, next_action: app.next_action ?? "", next_action_date: toLocalInput(app.next_action_date), follow_up_at: toLocalInput(app.follow_up_at) });
  }, [app?.id, app?.updated_at]); // eslint-disable-line
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["job", job.id] });
    qc.invalidateQueries({ queryKey: ["pipeline"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const add = useMutation({ mutationFn: () => api.post(`/jobs/${job.id}/application?stage=shortlisted`), onSuccess: refresh });
  const patch = useMutation({ mutationFn: (b: Record<string, unknown>) => api.patch<Application>(`/applications/${app!.id}`, b), onSuccess: refresh });
  const addContact = useMutation({
    mutationFn: () => api.post<Contact>(`/applications/${app!.id}/contacts`, contact),
    onSuccess: () => { setContact({ name: "", role: "", email: "", linkedin_url: "" }); refresh(); },
  });
  const delContact = useMutation({ mutationFn: (id: number) => api.del(`/contacts/${id}`), onSuccess: refresh });
  if (!app) return <Card><Empty>Not in your pipeline yet. <Button className="ml-2" onClick={() => add.mutate()} loading={add.isPending}>Add to pipeline</Button></Empty></Card>;
  const save = () =>
    patch.mutate({
      notes: form.notes, next_action: form.next_action,
      next_action_date: fromLocalInput(form.next_action_date), follow_up_at: fromLocalInput(form.follow_up_at),
      clear: [...(!form.next_action_date ? ["next_action_date"] : []), ...(!form.follow_up_at ? ["follow_up_at"] : [])],
    });
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Status & next steps" className="lg:col-span-2">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Stage">
            <select className={inputCls} value={app.stage} onChange={(e) => patch.mutate({ stage: e.target.value })}>
              {Object.entries(STAGE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          <Field label="Applied on"><div className="py-1.5 text-sm">{fmtDate(app.applied_at)}</div></Field>
          <Field label="Next action"><input className={inputCls} value={form.next_action} onChange={(e) => setForm({ ...form, next_action: e.target.value })} placeholder="e.g. Send thank-you note" /></Field>
          <Field label="Next action date"><input type="datetime-local" className={inputCls} value={form.next_action_date} onChange={(e) => setForm({ ...form, next_action_date: e.target.value })} /></Field>
          <Field label="Follow-up reminder"><input type="datetime-local" className={inputCls} value={form.follow_up_at} onChange={(e) => setForm({ ...form, follow_up_at: e.target.value })} /></Field>
        </div>
        <div className="mt-3"><Field label="Notes"><textarea className={inputCls + " h-32"} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field></div>
        <ErrorBox error={patch.error} />
        <div className="mt-3 flex justify-end"><Button variant="primary" onClick={save} loading={patch.isPending}>Save</Button></div>
      </Card>
      <div className="space-y-4">
        <Card title="Contacts">
          <ul className="space-y-2">
            {app.contacts?.map((c) => (
              <li key={c.id} className="flex items-start justify-between text-sm">
                <div>
                  <div className="font-medium">{c.name}</div>
                  <div className="text-xs text-slate-500">{[c.role, c.email].filter(Boolean).join(" · ")}</div>
                  {c.linkedin_url && <a className="text-xs text-brand-600 underline" href={c.linkedin_url} target="_blank" rel="noreferrer">LinkedIn</a>}
                </div>
                <button aria-label="Delete contact" onClick={() => delContact.mutate(c.id)} className="text-slate-400 hover:text-rose-600"><Trash2 className="h-4 w-4" /></button>
              </li>
            ))}
          </ul>
          <div className="mt-3 space-y-2">
            <input className={inputCls} placeholder="Name" value={contact.name} onChange={(e) => setContact({ ...contact, name: e.target.value })} />
            <input className={inputCls} placeholder="Role (Recruiter, HM…)" value={contact.role} onChange={(e) => setContact({ ...contact, role: e.target.value })} />
            <input className={inputCls} placeholder="Email" value={contact.email} onChange={(e) => setContact({ ...contact, email: e.target.value })} />
            <input className={inputCls} placeholder="LinkedIn URL" value={contact.linkedin_url} onChange={(e) => setContact({ ...contact, linkedin_url: e.target.value })} />
            <Button disabled={!contact.name} loading={addContact.isPending} onClick={() => addContact.mutate()}>Add contact</Button>
          </div>
        </Card>
        <Card title="History">
          <ol className="space-y-1 text-xs text-slate-600 dark:text-slate-400">
            {app.events?.map((e, i) => <li key={i}>{fmtDate(e.at, true)} — {e.from ? `${STAGE_LABELS[e.from]} → ` : ""}{STAGE_LABELS[e.to]}</li>)}
          </ol>
        </Card>
      </div>
    </div>
  );
}

function TailoredResume({ job, onGenerate, busy }: { job: JD; onGenerate: (force: boolean) => void; busy: boolean }) {
  const t = job.tailoring.resume;
  return (
    <Card
      title="Tailored resume"
      actions={
        <div className="flex flex-wrap gap-2">
          <a href={`/api/jobs/${job.id}/export/resume.docx`}><Button><Download className="h-4 w-4" /> .docx</Button></a>
          <a href={`/api/jobs/${job.id}/export/resume.pdf`}><Button><Download className="h-4 w-4" /> PDF</Button></a>
          <Button variant="primary" loading={busy} onClick={() => onGenerate(!!t)}><Sparkles className="h-4 w-4" /> {t ? "Regenerate" : "Tailor"}</Button>
        </div>
      }
    >
      {!t ? (
        <Empty>One click rewrites your existing bullets to emphasize what this JD asks for. Every bullet is traced to a real source bullet; invented metrics or skills are rejected. Exports use your base resume until you tailor.</Empty>
      ) : (
        <div className="space-y-4 text-sm">
          {t.stale && <Badge tone="amber">Profile or JD changed — regenerate to refresh</Badge>}
          <div><div className="text-xs font-semibold uppercase text-slate-500">Summary</div><p className="mt-1">{t.data.summary}</p></div>
          {t.data.experience.map((e: any) => (
            <div key={e.experience_id}>
              <div className="text-xs font-semibold uppercase text-slate-500">{e.experience_id}</div>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {e.bullets.map((b: any, i: number) => <li key={i}>{b.text} <span className="text-[10px] text-slate-400">[{b.source_bullet_ids.join(", ")}]</span></li>)}
              </ul>
            </div>
          ))}
          {!!t.data.keywords_covered?.length && <div className="flex flex-wrap gap-1">{t.data.keywords_covered.map((k: string) => <Badge key={k} tone="blue">{k}</Badge>)}</div>}
          {t.data.notes && <p className="text-xs text-slate-500">{t.data.notes}</p>}
        </div>
      )}
    </Card>
  );
}

function Kit({ job, onGenerate, busy }: { job: JD; onGenerate: (force: boolean) => void; busy: boolean }) {
  const t = job.tailoring.kit;
  if (!t)
    return (
      <Card title="Application kit" actions={<Button variant="primary" loading={busy} onClick={() => onGenerate(false)}><Sparkles className="h-4 w-4" /> Generate</Button>}>
        <Empty>Cover letter (&lt;250 words), "why this company" notes, 5 likely interview questions with talking points, and outreach messages.</Empty>
      </Card>
    );
  const d = t.data;
  return (
    <div className="space-y-4">
      <div className="flex justify-end gap-2">
        {t.stale && <Badge tone="amber">stale</Badge>}
        <Button loading={busy} onClick={() => onGenerate(true)}><RefreshCw className="h-4 w-4" /> Regenerate</Button>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Cover letter (${d.cover_letter.split(/\s+/).length} words)`} actions={<div className="flex gap-1"><CopyBtn text={d.cover_letter} /><a href={`/api/jobs/${job.id}/export/cover-letter.docx`}><Button variant="ghost"><Download className="h-3.5 w-3.5" /> .docx</Button></a></div>}>
          <p className="whitespace-pre-wrap text-sm">{d.cover_letter}</p>
        </Card>
        <div className="space-y-4">
          <Card title="Why this company">
            <ul className="list-disc space-y-1 pl-5 text-sm">{d.why_company.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
          </Card>
          <Card title="Outreach">
            <div className="space-y-3 text-sm">
              <div>
                <div className="flex items-center justify-between text-xs font-semibold uppercase text-slate-500">LinkedIn note ({d.outreach.linkedin_note.length}/300) <CopyBtn text={d.outreach.linkedin_note} /></div>
                <p className="mt-1">{d.outreach.linkedin_note}</p>
              </div>
              <div>
                <div className="flex items-center justify-between text-xs font-semibold uppercase text-slate-500">Email <CopyBtn text={`Subject: ${d.outreach.email_subject}\n\n${d.outreach.email_body}`} /></div>
                <p className="mt-1 font-medium">{d.outreach.email_subject}</p>
                <p className="whitespace-pre-wrap">{d.outreach.email_body}</p>
              </div>
            </div>
          </Card>
        </div>
      </div>
      <Card title="Likely interview questions">
        <ol className="space-y-3 text-sm">
          {d.interview_questions.map((q: any, i: number) => (
            <li key={i}>
              <div className="font-medium">{i + 1}. {q.question}</div>
              <div className="text-xs text-slate-500">{q.why_they_ask}</div>
              <ul className="mt-1 list-disc pl-5">{q.talking_points.map((p: string, j: number) => <li key={j}>{p}</li>)}</ul>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}

export default function JobDetail() {
  const { id } = useParams();
  const jobId = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Overview");
  const { data: job, isLoading, error } = useQuery({ queryKey: ["job", jobId], queryFn: () => api.get<JD>(`/jobs/${jobId}`) });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["job", jobId] });
    qc.invalidateQueries({ queryKey: ["jobs"] });
    qc.invalidateQueries({ queryKey: ["pipeline"] });
  };
  const score = useMutation({ mutationFn: (force: boolean) => api.post(`/jobs/${jobId}/score?force=${force}`), onSuccess: refresh });
  const tailor = useMutation({ mutationFn: ({ parts, force }: { parts: string; force: boolean }) => api.post(`/jobs/${jobId}/tailor?parts=${parts}&force=${force}`), onSuccess: refresh });
  const archive = useMutation({ mutationFn: (v: boolean) => api.patch(`/jobs/${jobId}`, { archived: v }), onSuccess: refresh });
  const practice = useMutation({
    mutationFn: () => api.post<Session>("/interviews", { kind: "company", job_id: jobId, num_questions: 4 }),
    onSuccess: (s) => nav(`/interview/${s.id}`),
  });
  const openApply = async () => {
    // Human in the loop: we only open the official page. Nothing is filled or submitted for you.
    const w = window.open(job?.apply_url || job?.url || "", "_blank", "noopener");
    await api.post(`/jobs/${jobId}/apply-opened`).catch(() => undefined);
    refresh();
    w?.focus();
  };

  if (isLoading) return <Spinner />;
  if (error || !job) return <ErrorBox error={error} />;
  const fit = job.fit;
  const parsed = fit?.parsed ?? job.parse;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/jobs" className="text-xs text-slate-500 hover:underline">← Jobs</Link>
          <h1 className="text-xl font-semibold">{job.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
            <span className="font-medium">{job.company}</span>·<span>{job.location || (job.is_remote ? "Remote" : "—")}</span>
            {job.comp_text && <>·<span>{job.comp_text}</span></>}
            {job.closed_at && <Badge tone="rose">closed {fmtDate(job.closed_at)}</Badge>}
            {job.stage && <Badge tone="violet">{STAGE_LABELS[job.stage]}</Badge>}
            {fit && <VerdictBadge verdict={fit.verdict} score={fit.score} method={fit.method} />}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={openApply} disabled={!job.apply_url && !job.url}><ExternalLink className="h-4 w-4" /> Open official apply page</Button>
          {job.application && job.stage !== "applied" && STAGE_LABELS[job.stage ?? ""] && ["discovered", "shortlisted"].includes(job.stage ?? "") && (
            <Button onClick={() => api.patch(`/applications/${job.application!.id}`, { stage: "applied" }).then(refresh)}>Mark applied</Button>
          )}
          <Button onClick={() => practice.mutate()} loading={practice.isPending}><GraduationCap className="h-4 w-4" /> Practice this loop</Button>
          <Button variant="ghost" onClick={() => archive.mutate(!job.archived)}>{job.archived ? "Unarchive" : "Archive"}</Button>
        </div>
      </div>
      <p className="text-xs text-slate-500">JobPilot never submits applications or fills forms — it prepares materials and opens the official posting so you apply yourself.</p>
      <ErrorBox error={score.error || tailor.error || practice.error} />

      <div className="flex gap-1 border-b border-slate-200 dark:border-slate-800">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={cx("-mb-px border-b-2 px-3 py-2 text-sm font-medium", tab === t ? "border-brand-600 text-brand-700 dark:text-sky-300" : "border-transparent text-slate-500 hover:text-slate-800")}>{t}</button>
        ))}
      </div>

      {tab === "Overview" && (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-1">
            <Card title="Fit" actions={<Button loading={score.isPending} onClick={() => score.mutate(!!fit)}>{fit ? <><RefreshCw className="h-3.5 w-3.5" /> Rescore</> : <><Sparkles className="h-3.5 w-3.5" /> Score</>}</Button>}>
              {!fit ? <Empty>Not scored yet.</Empty> : (
                <div className="space-y-3 text-sm">
                  <div className="flex items-center gap-2"><VerdictBadge verdict={fit.verdict} score={fit.score} method={fit.method} />{fit.stale && <Badge tone="amber">stale</Badge>}</div>
                  <p className="font-medium">{fit.verdict_line}</p>
                  <div><div className="text-xs font-semibold uppercase text-emerald-700">Strengths</div><ul className="list-disc pl-5">{fit.strengths.map((s, i) => <li key={i}>{s}</li>)}</ul></div>
                  <div><div className="text-xs font-semibold uppercase text-rose-700">Gaps</div><ul className="list-disc pl-5">{fit.gaps.map((s, i) => <li key={i}>{s}</li>)}</ul></div>
                  <div className="text-[11px] text-slate-400">{fit.method === "llm" ? fit.model : "keyword heuristic"} · {fmtDate(fit.created_at, true)}</div>
                </div>
              )}
            </Card>
            {parsed && (
              <Card title="Parsed JD">
                <dl className="space-y-2 text-sm">
                  <div><dt className="text-xs text-slate-500">Seniority / years</dt><dd>{parsed.seniority} · {parsed.years_experience_min ? `${parsed.years_experience_min}+ yrs` : "not stated"}</dd></div>
                  <div><dt className="text-xs text-slate-500">Location</dt><dd>{parsed.location ?? "—"} ({parsed.remote_policy})</dd></div>
                  <div><dt className="text-xs text-slate-500">Compensation</dt><dd>{parsed.comp_raw ?? (parsed.comp_min ? `${parsed.comp_min.toLocaleString()}–${parsed.comp_max?.toLocaleString()}` : "not listed")}</dd></div>
                  <div><dt className="text-xs text-slate-500">Required</dt><dd className="flex flex-wrap gap-1">{parsed.required_skills.map((s) => <Badge key={s}>{s}</Badge>)}</dd></div>
                  <div><dt className="text-xs text-slate-500">Nice to have</dt><dd className="flex flex-wrap gap-1">{parsed.nice_to_haves.map((s) => <Badge key={s} tone="blue">{s}</Badge>)}</dd></div>
                </dl>
              </Card>
            )}
            {!job.matches_filter && job.filter_reasons.length > 0 && (
              <Card title="Why it didn't match filters"><ul className="list-disc pl-5 text-sm">{job.filter_reasons.map((r) => <li key={r}>{r}</li>)}</ul></Card>
            )}
          </div>
          <Card title="Job description" className="lg:col-span-2" actions={job.url && <a href={job.url} target="_blank" rel="noreferrer" className="text-xs text-brand-600 underline">original posting</a>}>
            <div className="max-h-[70vh] overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed">{job.description_text}</div>
          </Card>
        </div>
      )}
      {tab === "Tailored resume" && <TailoredResume job={job} busy={tailor.isPending} onGenerate={(force) => tailor.mutate({ parts: "resume", force })} />}
      {tab === "Application kit" && <Kit job={job} busy={tailor.isPending} onGenerate={(force) => tailor.mutate({ parts: "kit", force })} />}
      {tab === "Tracking" && <Tracking job={job} />}
    </div>
  );
}

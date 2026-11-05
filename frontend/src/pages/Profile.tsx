import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Trash2 } from "lucide-react";
import { api, type Status } from "../api";
import { Badge, Button, Card, ErrorBox, Field, inputCls, Spinner } from "../components/ui";

interface Bullet { id: string; text: string }
interface Exp { id: string; company: string; title: string; location?: string | null; start?: string | null; end?: string | null; bullets: Bullet[] }
interface Resume { contact: { name: string; email?: string | null; phone?: string | null; location?: string | null; links: string[] }; summary: string; experience: Exp[]; education: { school: string; degree?: string | null; field?: string | null; year?: string | null }[]; skills: { category: string; items: string[] }[]; certifications: string[] }
interface ProfileData {
  name: string; headline: string; email: string | null; phone: string | null; location: string | null; links: string[];
  summary: string; highlights: string[]; target_roles: string[]; preferences: Record<string, unknown>;
  resume_text: string; structured_resume: Resume | null; structured_method: string | null; base_resume: Resume;
}

function ResumeEditor({ initial, onSave, saving }: { initial: Resume; onSave: (r: Resume) => void; saving: boolean }) {
  const [r, setR] = useState<Resume>(initial);
  useEffect(() => setR(initial), [initial]);
  const upExp = (i: number, patch: Partial<Exp>) => setR({ ...r, experience: r.experience.map((e, j) => (j === i ? { ...e, ...patch } : e)) });
  return (
    <div className="space-y-4">
      <Field label="Summary"><textarea className={inputCls + " h-20"} value={r.summary} onChange={(e) => setR({ ...r, summary: e.target.value })} /></Field>
      {r.experience.map((e, i) => (
        <div key={e.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
          <div className="grid gap-2 sm:grid-cols-4">
            <input className={inputCls} value={e.title} placeholder="Title" onChange={(x) => upExp(i, { title: x.target.value })} />
            <input className={inputCls} value={e.company} placeholder="Company" onChange={(x) => upExp(i, { company: x.target.value })} />
            <input className={inputCls} value={e.start ?? ""} placeholder="Start" onChange={(x) => upExp(i, { start: x.target.value })} />
            <input className={inputCls} value={e.end ?? ""} placeholder="End" onChange={(x) => upExp(i, { end: x.target.value })} />
          </div>
          <div className="mt-2 space-y-1.5">
            {e.bullets.map((b, k) => (
              <div key={b.id} className="flex gap-2">
                <span className="w-12 shrink-0 pt-1.5 text-[10px] text-slate-400">{b.id}</span>
                <textarea className={inputCls + " h-14"} value={b.text} onChange={(x) => upExp(i, { bullets: e.bullets.map((bb, kk) => (kk === k ? { ...bb, text: x.target.value } : bb)) })} />
                <button aria-label="Remove bullet" onClick={() => upExp(i, { bullets: e.bullets.filter((_, kk) => kk !== k) })} className="text-slate-400 hover:text-rose-600"><Trash2 className="h-4 w-4" /></button>
              </div>
            ))}
            <Button variant="ghost" onClick={() => upExp(i, { bullets: [...e.bullets, { id: `${e.id}b${Date.now() % 100000}`, text: "" }] })}><Plus className="h-3.5 w-3.5" /> Bullet</Button>
          </div>
        </div>
      ))}
      <Button variant="ghost" onClick={() => setR({ ...r, experience: [...r.experience, { id: `e${r.experience.length + 1}x${Date.now() % 1000}`, company: "", title: "", bullets: [] }] })}><Plus className="h-4 w-4" /> Experience</Button>
      <Field label="Skills (one group per line: Category: a, b, c)">
        <textarea
          className={inputCls + " h-24 font-mono text-xs"}
          value={r.skills.map((g) => `${g.category}: ${g.items.join(", ")}`).join("\n")}
          onChange={(e) => setR({ ...r, skills: e.target.value.split("\n").filter((l) => l.trim()).map((l) => { const [c, ...rest] = l.split(":"); return { category: c.trim(), items: rest.join(":").split(",").map((x) => x.trim()).filter(Boolean) }; }) })}
        />
      </Field>
      <div className="flex justify-end"><Button variant="primary" loading={saving} onClick={() => onSave(r)}>Save structured resume</Button></div>
    </div>
  );
}

export default function Profile() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["profile"], queryFn: () => api.get<ProfileData>("/profile") });
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: () => api.get<Status>("/status") });
  const [basic, setBasic] = useState({ name: "", headline: "", email: "", phone: "", location: "", summary: "", highlights: "", target_roles: "" });
  const [resume, setResume] = useState("");
  useEffect(() => {
    if (!data) return;
    setBasic({ name: data.name, headline: data.headline, email: data.email ?? "", phone: data.phone ?? "", location: data.location ?? "", summary: data.summary, highlights: data.highlights.join("\n"), target_roles: data.target_roles.join("\n") });
    setResume(data.resume_text);
  }, [data]);
  const done = () => { qc.invalidateQueries({ queryKey: ["profile"] }); qc.invalidateQueries({ queryKey: ["jobs"] }); };
  const saveBasic = useMutation({
    mutationFn: () => api.put("/profile", { ...basic, highlights: basic.highlights.split("\n").map((s) => s.trim()).filter(Boolean), target_roles: basic.target_roles.split("\n").map((s) => s.trim()).filter(Boolean) }),
    onSuccess: done,
  });
  const saveResume = useMutation({ mutationFn: () => api.post("/profile/resume", { resume_text: resume }), onSuccess: done });
  const saveStructured = useMutation({ mutationFn: (r: Resume) => api.put("/profile/structured", r), onSuccess: done });
  if (isLoading) return <Spinner />;
  if (error || !data) return <ErrorBox error={error} />;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Profile</h1>
      <p className="text-sm text-slate-500">Stored only in your local SQLite database ({status?.data_dir}). Everything the LLM writes is grounded in this profile — it never invents experience.</p>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Basics">
          <div className="grid gap-3 sm:grid-cols-2">
            {(["name", "headline", "email", "phone", "location"] as const).map((k) => (
              <Field key={k} label={k[0].toUpperCase() + k.slice(1)}><input className={inputCls} value={basic[k]} onChange={(e) => setBasic({ ...basic, [k]: e.target.value })} /></Field>
            ))}
          </div>
          <div className="mt-3 space-y-3">
            <Field label="Summary"><textarea className={inputCls + " h-24"} value={basic.summary} onChange={(e) => setBasic({ ...basic, summary: e.target.value })} /></Field>
            <Field label="Key facts (one per line)"><textarea className={inputCls + " h-28"} value={basic.highlights} onChange={(e) => setBasic({ ...basic, highlights: e.target.value })} /></Field>
            <Field label="Target roles (one per line)"><textarea className={inputCls + " h-20"} value={basic.target_roles} onChange={(e) => setBasic({ ...basic, target_roles: e.target.value })} /></Field>
          </div>
          <ErrorBox error={saveBasic.error} />
          <div className="mt-3 flex justify-end"><Button variant="primary" loading={saveBasic.isPending} onClick={() => saveBasic.mutate()}>Save</Button></div>
        </Card>
        <Card title="Full resume" actions={data.structured_method && <Badge tone="blue">structured: {data.structured_method}</Badge>}>
          <Field label="Paste your full resume (plain text)" hint="Saved locally and structured into experiences/bullets with stable ids (LLM if configured, otherwise a heuristic parser you can correct below).">
            <textarea className={inputCls + " h-80 font-mono text-xs"} value={resume} onChange={(e) => setResume(e.target.value)} />
          </Field>
          <ErrorBox error={saveResume.error} />
          <div className="mt-3 flex flex-wrap justify-between gap-2">
            <div className="flex gap-2">
              <a href="/api/profile/export/resume.docx"><Button><Download className="h-4 w-4" /> Base .docx</Button></a>
              <a href="/api/profile/export/resume.pdf"><Button><Download className="h-4 w-4" /> Base PDF</Button></a>
            </div>
            <Button variant="primary" disabled={resume.trim().length < 50} loading={saveResume.isPending} onClick={() => saveResume.mutate()}>Save & structure</Button>
          </div>
        </Card>
      </div>
      <Card title="Structured resume (source for tailoring & exports)">
        <ErrorBox error={saveStructured.error} />
        <ResumeEditor initial={data.structured_resume ?? data.base_resume} saving={saveStructured.isPending} onSave={(r) => saveStructured.mutate(r)} />
      </Card>
      {status && (
        <Card title="Settings (from .env)">
          <dl className="grid gap-2 text-sm sm:grid-cols-3">
            <div><dt className="text-xs text-slate-500">LLM provider</dt><dd>{status.llm.configured ? status.llm.provider : "not configured"}</dd></div>
            <div><dt className="text-xs text-slate-500">JD parse model</dt><dd>{status.llm.model_parse}</dd></div>
            <div><dt className="text-xs text-slate-500">Scoring / tailoring model</dt><dd>{status.llm.model_default}</dd></div>
            <div><dt className="text-xs text-slate-500">Interviewer model</dt><dd>{status.llm.model_interview}</dd></div>
            <div><dt className="text-xs text-slate-500">Background budget</dt><dd>${status.llm.daily_budget_usd}/day (spent ${status.llm.spent_24h_usd.toFixed(2)})</dd></div>
            <div><dt className="text-xs text-slate-500">Discovery</dt><dd>{status.discovery.schedule_enabled ? status.discovery.schedule : "schedule disabled"}</dd></div>
          </dl>
        </Card>
      )}
    </div>
  );
}

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Plus } from "lucide-react";
import { api, fmtDate, KIND_LABELS, qs, type Question, type Session } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Field, inputCls, Modal, Spinner } from "../components/ui";

const DIFF = ["", "Easy", "Medium", "Hard"];

export default function Questions() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [f, setF] = useState({ category: "", difficulty: "", due: false });
  const { data, isLoading, error } = useQuery({
    queryKey: ["questions", f],
    queryFn: () => api.get<{ questions: Question[]; topics: { category: string; topic: string; avg_score: number | null; due: number }[] }>("/questions" + qs({ category: f.category, difficulty: f.difficulty, due: f.due || undefined })),
  });
  const [adding, setAdding] = useState(false);
  const [nq, setNq] = useState({ category: "system_design", topic: "", difficulty: 2, prompt: "", rubric_hints: "" });
  const add = useMutation({
    mutationFn: () => api.post("/questions", { ...nq, rubric_hints: nq.rubric_hints.split("\n").filter(Boolean) }),
    onSuccess: () => { setAdding(false); qc.invalidateQueries({ queryKey: ["questions"] }); },
  });
  const toggle = useMutation({ mutationFn: (q: Question) => api.patch(`/questions/${q.id}`, { active: !q.active }), onSuccess: () => qc.invalidateQueries({ queryKey: ["questions"] }) });
  const practice = useMutation({
    mutationFn: (q: Question) => api.post<Session>("/interviews", { kind: q.category, question_ids: [q.id], num_questions: 1 }),
    onSuccess: (s) => nav(`/interview/${s.id}`),
  });
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Question Bank</h1>
        <Button variant="primary" onClick={() => setAdding(true)}><Plus className="h-4 w-4" /> Add question</Button>
      </div>
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Category">
            <select className={inputCls} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
              <option value="">All</option>{["system_design", "ml_fundamentals", "behavioral", "coding"].map((c) => <option key={c} value={c}>{KIND_LABELS[c]}</option>)}
            </select>
          </Field>
          <Field label="Difficulty">
            <select className={inputCls} value={f.difficulty} onChange={(e) => setF({ ...f, difficulty: e.target.value })}>
              <option value="">Any</option><option value="1">Easy</option><option value="2">Medium</option><option value="3">Hard</option>
            </select>
          </Field>
          <label className="flex items-center gap-1.5 pb-2 text-sm"><input type="checkbox" checked={f.due} onChange={(e) => setF({ ...f, due: e.target.checked })} /> Due for review</label>
        </div>
      </Card>
      <ErrorBox error={practice.error} />
      {isLoading ? <Spinner /> : error ? <ErrorBox error={error} /> : !data?.questions.length ? <Empty>No questions match.</Empty> : (
        <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {data.questions.map((q) => (
              <li key={q.id} className={"flex items-start gap-3 px-4 py-3 " + (q.active ? "" : "opacity-50")}>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5 text-xs">
                    <Badge tone="blue">{KIND_LABELS[q.category]}</Badge><Badge>{q.topic}</Badge><Badge tone={q.difficulty === 3 ? "rose" : q.difficulty === 2 ? "amber" : "green"}>{DIFF[q.difficulty]}</Badge>
                    {q.source !== "seed" && <Badge tone="violet">{q.source}</Badge>}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-sm">{q.prompt.split("\n")[0]}</p>
                  {q.review && (
                    <div className="mt-1 text-xs text-slate-500">
                      Last {q.review.last_score?.toFixed(1) ?? "—"}/10 · {q.review.attempts} attempt(s) · next review {fmtDate(q.review.due_at)}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button variant="ghost" onClick={() => toggle.mutate(q)}>{q.active ? "Retire" : "Restore"}</Button>
                  <Button onClick={() => practice.mutate(q)} loading={practice.isPending && practice.variables?.id === q.id}><Play className="h-3.5 w-3.5" /> Practice</Button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      <Modal open={adding} onClose={() => setAdding(false)} title="Add a question">
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <Field label="Category">
              <select className={inputCls} value={nq.category} onChange={(e) => setNq({ ...nq, category: e.target.value })}>
                {["system_design", "ml_fundamentals", "behavioral"].map((c) => <option key={c} value={c}>{KIND_LABELS[c]}</option>)}
              </select>
            </Field>
            <Field label="Topic"><input className={inputCls} value={nq.topic} onChange={(e) => setNq({ ...nq, topic: e.target.value })} /></Field>
            <Field label="Difficulty">
              <select className={inputCls} value={nq.difficulty} onChange={(e) => setNq({ ...nq, difficulty: Number(e.target.value) })}>
                <option value={1}>Easy</option><option value={2}>Medium</option><option value={3}>Hard</option>
              </select>
            </Field>
          </div>
          <Field label="Question"><textarea className={inputCls + " h-24"} value={nq.prompt} onChange={(e) => setNq({ ...nq, prompt: e.target.value })} /></Field>
          <Field label="What a strong answer covers (one per line, optional)"><textarea className={inputCls + " h-20"} value={nq.rubric_hints} onChange={(e) => setNq({ ...nq, rubric_hints: e.target.value })} /></Field>
          <ErrorBox error={add.error} />
          <div className="flex justify-end"><Button variant="primary" disabled={!nq.topic || nq.prompt.length < 10} loading={add.isPending} onClick={() => add.mutate()}>Add</Button></div>
        </div>
      </Modal>
    </div>
  );
}

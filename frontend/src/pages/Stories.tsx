import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { api, type Status, type Story } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Field, inputCls, Modal, Spinner } from "../components/ui";

interface StoriesData { stories: Story[]; behavioral_questions: { key: string; prompt: string; topic: string }[]; competencies: string[]; coverage: Record<string, number[]>; project_fields: string[] }

const FIELD_HINTS: Record<string, string> = {
  context: "What was the situation / business problem?", role: "Your role (e.g. architect, sole engineer)", actions: "What YOU did, technically and with people",
  outcome: "What happened as a result", metrics: "Real numbers only (users, latency, $…)", tech: "Stack / techniques", challenges: "Hard parts, conflicts, trade-offs",
};

function StoryForm({ story, data, onClose }: { story: Story | null; data: StoriesData; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: () => api.get<Status>("/status") });
  const [title, setTitle] = useState(story?.title ?? "");
  const [project, setProject] = useState<Record<string, string>>(story?.project ?? {});
  const [star, setStar] = useState({ situation: story?.situation ?? "", task: story?.task ?? "", action: story?.action ?? "", result: story?.result ?? "" });
  const [qids, setQids] = useState<string[]>(story?.question_ids ?? []);
  const save = useMutation({
    mutationFn: (generate: boolean) => {
      const body: Record<string, unknown> = { title, project, question_ids: qids };
      if (!generate) Object.assign(body, star);
      return story ? api.put<Story>(`/stories/${story.id}`, body).then((s) => (generate ? api.post<Story>(`/stories/${s.id}/generate`) : s)) : api.post<Story>(`/stories?generate=${generate}`, body);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["stories"] }); onClose(); },
  });
  return (
    <div className="space-y-3">
      <Field label="Project title"><input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Supervisor–subagent orchestration at Workday" /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        {data.project_fields.map((f) => (
          <Field key={f} label={f[0].toUpperCase() + f.slice(1)} hint={FIELD_HINTS[f]}>
            <textarea className={inputCls + " h-20"} value={project[f] ?? ""} onChange={(e) => setProject({ ...project, [f]: e.target.value })} />
          </Field>
        ))}
      </div>
      <details className="rounded-md border border-slate-200 p-3 dark:border-slate-700">
        <summary className="cursor-pointer text-sm font-medium">Write / edit STAR manually</summary>
        <div className="mt-3 grid gap-3">
          {(["situation", "task", "action", "result"] as const).map((k) => (
            <Field key={k} label={k.toUpperCase()}><textarea className={inputCls + " h-20"} value={star[k]} onChange={(e) => setStar({ ...star, [k]: e.target.value })} /></Field>
          ))}
          <Field label="Answers these questions">
            <div className="flex flex-wrap gap-1.5">
              {data.behavioral_questions.map((q) => (
                <button key={q.key} title={q.prompt} onClick={() => setQids(qids.includes(q.key) ? qids.filter((x) => x !== q.key) : [...qids, q.key])}>
                  <Badge tone={qids.includes(q.key) ? "violet" : "slate"}>{q.key.replace("beh-", "")}</Badge>
                </button>
              ))}
            </div>
          </Field>
        </div>
      </details>
      <ErrorBox error={save.error} />
      <div className="flex justify-end gap-2">
        <Button onClick={() => save.mutate(false)} loading={save.isPending} disabled={!title}>Save as written</Button>
        <Button variant="primary" onClick={() => save.mutate(true)} loading={save.isPending} disabled={!title || !status?.llm.configured} title={status?.llm.configured ? "" : "Needs OPENAI_API_KEY"}>
          <Sparkles className="h-4 w-4" /> Save & generate STAR
        </Button>
      </div>
    </div>
  );
}

export default function Stories() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["stories"], queryFn: () => api.get<StoriesData>("/stories") });
  const [editing, setEditing] = useState<Story | null | "new">(null);
  const del = useMutation({ mutationFn: (id: number) => api.del(`/stories/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["stories"] }) });
  if (isLoading) return <Spinner />;
  if (error || !data) return <ErrorBox error={error} />;
  const uncovered = data.behavioral_questions.filter((q) => !data.coverage[q.key]?.length);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Story Bank</h1>
        <Button variant="primary" onClick={() => setEditing("new")}><Plus className="h-4 w-4" /> Add project</Button>
      </div>
      <p className="text-sm text-slate-500">Enter each project once. The LLM turns it into a STAR story (using only your facts) and maps it to common behavioral questions; behavioral mock interviews probe against these stories.</p>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-2">
          {!data.stories.length && <Empty>No stories yet. Start with your multi-agent orchestration system and the A2A action-execution service.</Empty>}
          {data.stories.map((s) => (
            <Card key={s.id} title={s.title} actions={
              <div className="flex gap-1">
                <Badge>{s.method}</Badge>
                <Button variant="ghost" aria-label="Edit" onClick={() => setEditing(s)}><Pencil className="h-3.5 w-3.5" /></Button>
                <Button variant="ghost" aria-label="Delete" onClick={() => confirm("Delete this story?") && del.mutate(s.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
              </div>
            }>
              {s.situation ? (
                <dl className="space-y-2 text-sm">
                  {(["situation", "task", "action", "result"] as const).map((k) => (
                    <div key={k}><dt className="text-xs font-semibold uppercase text-slate-500">{k}</dt><dd className="whitespace-pre-wrap">{s[k]}</dd></div>
                  ))}
                </dl>
              ) : <p className="text-sm text-slate-500">No STAR yet — edit to generate or write it.</p>}
              <div className="mt-3 flex flex-wrap gap-1">
                {s.competencies.map((c) => <Badge key={c} tone="blue">{c}</Badge>)}
                {s.question_ids.map((q) => <Badge key={q} tone="violet">{q.replace("beh-", "")}</Badge>)}
              </div>
            </Card>
          ))}
        </div>
        <Card title="Question coverage">
          <ul className="space-y-1.5 text-sm">
            {data.behavioral_questions.map((q) => (
              <li key={q.key} className="flex items-start justify-between gap-2">
                <span className="text-slate-700 dark:text-slate-300">{q.prompt}</span>
                {data.coverage[q.key]?.length ? <Badge tone="green">{data.coverage[q.key].length}</Badge> : <Badge tone="rose">0</Badge>}
              </li>
            ))}
          </ul>
          {!!uncovered.length && <p className="mt-3 text-xs text-amber-700">{uncovered.length} question(s) have no story yet.</p>}
        </Card>
      </div>
      <Modal open={editing !== null} onClose={() => setEditing(null)} title={editing === "new" ? "New project / story" : "Edit story"} wide>
        {editing !== null && <StoryForm story={editing === "new" ? null : editing} data={data} onClose={() => setEditing(null)} />}
      </Modal>
    </div>
  );
}

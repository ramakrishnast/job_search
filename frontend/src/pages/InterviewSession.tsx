import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Mic, MicOff, PenTool, Play, Send, SkipForward, Timer, XCircle } from "lucide-react";
import { api, KIND_LABELS, type CodeResult, type Session, type Turn } from "../api";
import { Badge, Button, Card, cx, ErrorBox, inputCls, MiniMarkdown, Spinner } from "../components/ui";
import type { WhiteboardHandle } from "../components/Whiteboard";
import { useSpeech } from "../components/useSpeech";

const Whiteboard = lazy(() => import("../components/Whiteboard"));
const CodeEditor = lazy(() => import("../components/CodeEditor"));

function CodeResultView({ r }: { r: CodeResult }) {
  return (
    <div className="mt-2 rounded-md bg-slate-50 p-2 text-xs dark:bg-slate-800/60">
      <div className={cx("font-semibold", r.ok ? "text-emerald-700" : "text-rose-700")}>
        {r.error ? "Error" : `${r.passed}/${r.total} tests passed`} <span className="font-normal text-slate-500">· {r.duration_ms} ms</span>
      </div>
      {r.error && <pre className="mt-1 whitespace-pre-wrap text-rose-700">{r.error}</pre>}
      <ul className="mt-1 space-y-0.5">
        {r.results.map((t) => (
          <li key={t.name} className="flex items-start gap-1">
            {t.passed ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" /> : <XCircle className="h-3.5 w-3.5 shrink-0 text-rose-600" />}
            <span className="font-mono">{t.name}</span>
            {t.error && <span className="text-rose-700"> — {t.error}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Bubble({ t }: { t: Turn }) {
  const me = t.role === "candidate";
  return (
    <div className={cx("flex", me ? "justify-end" : "justify-start")}>
      <div className={cx("max-w-[85%] rounded-2xl px-4 py-2.5 text-sm", me ? "bg-brand-600 text-white" : "bg-white ring-1 ring-slate-200 dark:bg-slate-800 dark:ring-slate-700")}>
        <div className="prose-chat"><MiniMarkdown text={t.content} /></div>
        {t.code && <pre className={cx("mt-2 max-h-60 overflow-auto rounded-md p-2 text-xs", me ? "bg-brand-700" : "bg-slate-100 dark:bg-slate-900")}>{t.code}</pre>}
        {t.code_result && <div className="text-slate-800 dark:text-slate-200"><CodeResultView r={t.code_result} /></div>}
        {t.whiteboard_image && <img src={t.whiteboard_image} alt="whiteboard" className="mt-2 max-h-64 rounded-md bg-white" />}
        {(t.time_taken_s || t.input_mode === "voice") && (
          <div className={cx("mt-1 text-[10px]", me ? "text-sky-100" : "text-slate-400")}>
            {t.time_taken_s ? `${Math.floor(t.time_taken_s / 60)}m ${t.time_taken_s % 60}s` : ""} {t.input_mode === "voice" ? "· voice" : ""}
          </div>
        )}
      </div>
    </div>
  );
}

function Scorecard({ s }: { s: Session }) {
  const c = s.scorecard!;
  const tone = c.overall_score >= 75 ? "green" : c.overall_score >= 55 ? "amber" : "rose";
  return (
    <div className="space-y-4">
      <Card title="Scorecard" actions={<Badge tone={tone}>{c.hire_signal.replace(/_/g, " ")}</Badge>}>
        <div className="flex flex-wrap items-center gap-6">
          <div className="text-4xl font-bold tabular-nums">{c.overall_score}<span className="text-base font-normal text-slate-500">/100</span></div>
          <div className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
            {Object.entries(c.rubric).map(([k, v]) => (
              <div key={k}>
                <div className="text-xs capitalize text-slate-500">{k.replace("_", " ")}</div>
                <div className="flex items-center gap-2">
                  <div className="h-2 flex-1 rounded-full bg-slate-200 dark:bg-slate-700"><div className="h-2 rounded-full bg-brand-500" style={{ width: `${v.score * 10}%` }} /></div>
                  <span className="text-sm font-semibold tabular-nums">{v.score}</span>
                </div>
                <div className="mt-0.5 text-[11px] text-slate-500">{v.justification}</div>
              </div>
            ))}
          </div>
        </div>
        <p className="mt-3 text-sm">{c.summary}</p>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div><div className="text-xs font-semibold uppercase text-emerald-700">Strengths</div><ul className="list-disc pl-5 text-sm">{c.strengths.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
          <div><div className="text-xs font-semibold uppercase text-rose-700">Gaps</div><ul className="list-disc pl-5 text-sm">{c.gaps.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
        </div>
        {!!c.topics_to_review.length && <div className="mt-3 flex flex-wrap gap-1">{c.topics_to_review.map((t) => <Badge key={t} tone="amber">{t}</Badge>)}</div>}
      </Card>
      {c.per_question.map((q) => (
        <Card key={q.question_index} title={`Q${q.question_index + 1}: ${s.questions?.[q.question_index]?.prompt.split("\n")[0] ?? ""}`} actions={<Badge tone={q.score >= 7 ? "green" : q.score >= 5 ? "amber" : "rose"}>{q.score}/10</Badge>}>
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            <div><div className="text-xs font-semibold uppercase text-slate-500">Went well</div><ul className="list-disc pl-5">{q.went_well.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
            <div><div className="text-xs font-semibold uppercase text-slate-500">To improve</div><ul className="list-disc pl-5">{q.to_improve.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
          </div>
          <details className="mt-3"><summary className="cursor-pointer text-sm font-medium text-brand-700 dark:text-sky-300">Model answer</summary><p className="mt-2 whitespace-pre-wrap text-sm">{q.model_answer}</p></details>
        </Card>
      ))}
    </div>
  );
}

export default function InterviewSession() {
  const { id } = useParams();
  const sid = Number(id);
  const qc = useQueryClient();
  const { data: s, isLoading, error } = useQuery({ queryKey: ["interview", sid], queryFn: () => api.get<Session>(`/interviews/${sid}`) });
  const [text, setText] = useState("");
  const [code, setCode] = useState("");
  const [showBoard, setShowBoard] = useState(false);
  const [runResult, setRunResult] = useState<CodeResult | null>(null);
  const [usedVoice, setUsedVoice] = useState(false);
  const [startedAt, setStartedAt] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const board = useRef<WhiteboardHandle>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const speech = useSpeech((t) => {
    setText((prev) => (prev ? prev + " " : "") + t);
    setUsedVoice(true);
  });

  const current = s && s.current_index < s.num_questions ? s.questions?.[s.current_index] : undefined;
  const isCoding = current?.category === "coding" && current.has_tests;
  const isDesign = current?.category === "system_design" || s?.kind === "system_design";
  const lastTurnId = s?.turns?.[s.turns.length - 1]?.id;

  useEffect(() => {
    setStartedAt(Date.now());
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [lastTurnId]);
  useEffect(() => {
    if (current?.starter_code && isCoding) setCode((c) => c || current.starter_code || "");
    setRunResult(null);
    if (isDesign) setShowBoard(true);
  }, [current?.id]); // eslint-disable-line
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const refresh = (data: Session) => qc.setQueryData(["interview", sid], data);
  const answer = useMutation({
    mutationFn: async () => {
      speech.stop();
      const png = showBoard && board.current ? await board.current.exportPng() : null;
      return api.post<Session>(`/interviews/${sid}/answer`, {
        text,
        code: isCoding ? code : null,
        whiteboard_png: png,
        whiteboard_scene: png ? board.current?.scene() : null,
        time_taken_s: Math.round((Date.now() - startedAt) / 1000),
        input_mode: usedVoice ? (text ? "mixed" : "voice") : "text",
      });
    },
    onSuccess: (d) => {
      const advanced = d.current_index !== s?.current_index;
      refresh(d);
      setText("");
      setUsedVoice(false);
      if (advanced) {
        setCode("");
        board.current?.clear();
      }
    },
  });
  const skip = useMutation({ mutationFn: () => api.post<Session>(`/interviews/${sid}/skip`), onSuccess: (d) => { refresh(d); setCode(""); setText(""); } });
  const finish = useMutation({
    mutationFn: () => api.post<Session>(`/interviews/${sid}/finish`),
    onSuccess: (d) => { refresh(d); qc.invalidateQueries({ queryKey: ["interviews"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); },
  });
  const run = useMutation({ mutationFn: () => api.post<CodeResult>("/code/run", { question_id: current!.id, code }), onSuccess: setRunResult });

  const timer = s?.config.timer_seconds;
  const elapsed = Math.floor((now - startedAt) / 1000);
  const remaining = timer ? timer - elapsed : null;
  const canAnswer = s?.status === "active" && s.current_index < s.num_questions;
  const hasContent = text.trim().length > 0 || (isCoding && code.trim().length > 0) || showBoard;

  const progress = useMemo(() => (s ? `${Math.min(s.current_index + 1, s.num_questions)} / ${s.num_questions}` : ""), [s]);
  if (isLoading) return <Spinner />;
  if (error || !s) return <ErrorBox error={error} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link to="/interview" className="text-xs text-slate-500 hover:underline">← Interviews</Link>
          <h1 className="text-xl font-semibold">{KIND_LABELS[s.kind]} interview</h1>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone="blue">Question {progress}</Badge>
          {canAnswer && remaining !== null && (
            <Badge tone={remaining < 0 ? "rose" : remaining < 60 ? "amber" : "slate"} className="tabular-nums">
              <Timer className="mr-1 h-3 w-3" /> {remaining < 0 ? "-" : ""}{Math.floor(Math.abs(remaining) / 60)}:{String(Math.abs(remaining) % 60).padStart(2, "0")}
            </Badge>
          )}
          {canAnswer && !timer && <Badge className="tabular-nums"><Timer className="mr-1 h-3 w-3" />{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</Badge>}
          {s.status === "active" && <Button variant="primary" onClick={() => finish.mutate()} loading={finish.isPending}>Finish & score</Button>}
        </div>
      </div>
      <ErrorBox error={answer.error || finish.error || skip.error} />

      {s.status === "completed" && s.scorecard && <Scorecard s={s} />}
      {s.status === "abandoned" && <ErrorBox error="Session ended without answers — nothing to score." />}

      <div className={cx("grid gap-4", (showBoard || isCoding) && canAnswer ? "lg:grid-cols-2" : "")}>
        <Card className="flex flex-col">
          <div className="max-h-[60vh] min-h-64 space-y-3 overflow-y-auto pr-1">
            {s.turns?.map((t) => <Bubble key={t.id} t={t} />)}
            {answer.isPending && <div className="text-sm text-slate-500">Interviewer is thinking…</div>}
            <div ref={bottom} />
          </div>
          {canAnswer && (
            <div className="mt-3 space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
              <textarea
                className={inputCls + " h-32"}
                placeholder={isCoding ? "Explain your approach, complexity, edge cases…" : "Type your answer, or use the mic to speak it…"}
                value={text + (speech.interim ? (text ? " " : "") + speech.interim : "")}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && hasContent) answer.mutate();
                }}
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex gap-2">
                  {speech.supported ? (
                    <Button onClick={() => (speech.listening ? speech.stop() : speech.start())} className={speech.listening ? "ring-rose-400" : ""}>
                      {speech.listening ? <><MicOff className="h-4 w-4 text-rose-600" /> Stop</> : <><Mic className="h-4 w-4" /> Speak</>}
                    </Button>
                  ) : (
                    <span className="text-xs text-slate-400">Voice input needs Chrome, Edge or Safari</span>
                  )}
                  {!isCoding && <Button onClick={() => setShowBoard(!showBoard)}><PenTool className="h-4 w-4" /> {showBoard ? "Hide" : "Whiteboard"}</Button>}
                  <Button variant="ghost" onClick={() => skip.mutate()} loading={skip.isPending}><SkipForward className="h-4 w-4" /> Skip</Button>
                </div>
                <Button variant="primary" disabled={!hasContent} loading={answer.isPending} onClick={() => answer.mutate()}><Send className="h-4 w-4" /> Send <span className="hidden text-[10px] opacity-70 sm:inline">⌘↵</span></Button>
              </div>
              {showBoard && <p className="text-[11px] text-slate-500">Your diagram is sent with the answer as an image — the interviewer and the scorecard judge it.</p>}
            </div>
          )}
        </Card>

        {canAnswer && isCoding && (
          <Card title={`Code · ${current?.function_name ?? ""}`} actions={<Button onClick={() => run.mutate()} loading={run.isPending}><Play className="h-4 w-4" /> Run tests</Button>}>
            <Suspense fallback={<Spinner />}><CodeEditor value={code} onChange={setCode} height="420px" /></Suspense>
            {runResult && <CodeResultView r={runResult} />}
            <ErrorBox error={run.error} />
            <p className="mt-2 text-[11px] text-slate-500">Tests run locally in an isolated subprocess. Sending your answer re-runs them and shares the code + results with the interviewer.</p>
          </Card>
        )}
        {canAnswer && !isCoding && showBoard && (
          <Card title="Whiteboard">
            <Suspense fallback={<Spinner label="Loading whiteboard…" />}><Whiteboard ref={board} height={480} /></Suspense>
          </Card>
        )}
      </div>
    </div>
  );
}

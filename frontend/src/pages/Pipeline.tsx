import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DndContext, DragOverlay, PointerSensor, KeyboardSensor, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent, type DragStartEvent } from "@dnd-kit/core";
import { CalendarClock, GripVertical } from "lucide-react";
import { api, fmtDate, parseTs, STAGE_LABELS, type JobSummary } from "../api";
import { Badge, cx, ErrorBox, Spinner, VerdictBadge } from "../components/ui";

type Item = JobSummary & { application: NonNullable<JobSummary["application"]> };
interface Board { stages: string[]; columns: Record<string, Item[]> }

function CardBody({ item, dragging }: { item: Item; dragging?: boolean }) {
  const a = item.application;
  const due = a.follow_up_at || a.next_action_date;
  const overdue = due && parseTs(due) < new Date();
  return (
    <div className={cx("rounded-lg bg-white p-2.5 text-sm shadow-sm ring-1 ring-slate-200 dark:bg-slate-800 dark:ring-slate-700", dragging && "rotate-1 shadow-lg ring-brand-500")}>
      <div className="flex items-start gap-1">
        <GripVertical className="mt-0.5 h-4 w-4 shrink-0 text-slate-300" />
        <div className="min-w-0 flex-1">
          <Link to={`/jobs/${item.id}`} className="line-clamp-2 font-medium hover:underline" onPointerDown={(e) => e.stopPropagation()}>{item.title}</Link>
          <div className="text-xs text-slate-500">{item.company}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {item.fit && <VerdictBadge verdict={item.fit.verdict} score={item.fit.score} method={item.fit.method} />}
            {item.closed_at && <Badge tone="rose">closed</Badge>}
          </div>
          {(a.next_action || due) && (
            <div className={cx("mt-1.5 flex items-center gap-1 text-[11px]", overdue ? "text-rose-600" : "text-slate-500")}>
              <CalendarClock className="h-3 w-3" /> {a.next_action || "Follow up"} {due && `· ${fmtDate(due)}`}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DraggableCard({ item }: { item: Item }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: item.application.id, data: { item } });
  return (
    <div ref={setNodeRef} {...listeners} {...attributes} className={cx("cursor-grab touch-none", isDragging && "opacity-30")}>
      <CardBody item={item} />
    </div>
  );
}

function Column({ stage, items }: { stage: string; items: Item[] }) {
  const { setNodeRef, isOver } = useDroppable({ id: stage });
  const [limit, setLimit] = useState(30);
  return (
    <div ref={setNodeRef} className={cx("flex w-64 shrink-0 flex-col rounded-xl bg-slate-100 p-2 dark:bg-slate-900", isOver && "ring-2 ring-brand-500")}>
      <div className="mb-2 flex items-center justify-between px-1">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">{STAGE_LABELS[stage]}</h3>
        <Badge>{items.length}</Badge>
      </div>
      <div className="flex min-h-24 flex-1 flex-col gap-2 overflow-y-auto">
        {items.slice(0, limit).map((it) => <DraggableCard key={it.application.id} item={it} />)}
        {items.length > limit && <button className="text-xs text-brand-600" onClick={() => setLimit(limit + 30)}>Show more ({items.length - limit})</button>}
      </div>
    </div>
  );
}

export default function Pipeline() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["pipeline"], queryFn: () => api.get<Board>("/pipeline") });
  const [active, setActive] = useState<Item | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }), useSensor(KeyboardSensor));
  const move = useMutation({
    mutationFn: ({ appId, stage }: { appId: number; stage: string }) => api.patch(`/applications/${appId}`, { stage, position: -Date.now() / 1000 }),
    onMutate: async ({ appId, stage }) => {
      await qc.cancelQueries({ queryKey: ["pipeline"] });
      const prev = qc.getQueryData<Board>(["pipeline"]);
      if (prev) {
        const cols = Object.fromEntries(Object.entries(prev.columns).map(([k, v]) => [k, v.filter((x) => x.application.id !== appId)]));
        const moved = Object.values(prev.columns).flat().find((x) => x.application.id === appId);
        if (moved) cols[stage] = [{ ...moved, stage, application: { ...moved.application, stage } }, ...cols[stage]];
        qc.setQueryData(["pipeline"], { ...prev, columns: cols });
      }
      return { prev };
    },
    onError: (_e, _v, ctx) => ctx?.prev && qc.setQueryData(["pipeline"], ctx.prev),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["pipeline"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
  const total = useMemo(() => (data ? Object.values(data.columns).reduce((n, c) => n + c.length, 0) : 0), [data]);

  const onStart = (e: DragStartEvent) => setActive((e.active.data.current as { item: Item }).item);
  const onEnd = (e: DragEndEvent) => {
    setActive(null);
    const item = (e.active.data.current as { item: Item } | undefined)?.item;
    const to = e.over?.id as string | undefined;
    if (item && to && to !== item.application.stage) move.mutate({ appId: item.application.id, stage: to });
  };

  if (isLoading) return <Spinner />;
  if (error || !data) return <ErrorBox error={error} />;
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold">Pipeline <span className="text-sm font-normal text-slate-500">({total})</span></h1>
        <p className="text-xs text-slate-500">Drag cards between stages. New matching postings land in Discovered automatically.</p>
      </div>
      <ErrorBox error={move.error} />
      <DndContext sensors={sensors} onDragStart={onStart} onDragEnd={onEnd} onDragCancel={() => setActive(null)}>
        <div className="flex gap-3 overflow-x-auto pb-4" style={{ minHeight: "70vh" }}>
          {data.stages.map((s) => <Column key={s} stage={s} items={data.columns[s] ?? []} />)}
        </div>
        <DragOverlay>{active ? <CardBody item={active} dragging /> : null}</DragOverlay>
      </DndContext>
    </div>
  );
}

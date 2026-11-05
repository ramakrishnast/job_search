import { lazy, Suspense, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, BookOpen, Briefcase, Building2, GraduationCap, KanbanSquare, LayoutDashboard, ListChecks, Menu, User } from "lucide-react";
import { api, fmtDate, type Notification, type Status } from "./api";
import { cx, Spinner } from "./components/ui";
import Dashboard from "./pages/Dashboard";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import Pipeline from "./pages/Pipeline";
import Companies from "./pages/Companies";
import Profile from "./pages/Profile";
import Stories from "./pages/Stories";
import Questions from "./pages/Questions";
import InterviewHome from "./pages/InterviewHome";

const InterviewSession = lazy(() => import("./pages/InterviewSession"));

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/jobs", label: "Jobs", icon: Briefcase },
  { to: "/pipeline", label: "Pipeline", icon: KanbanSquare },
  { to: "/interview", label: "Interview", icon: GraduationCap },
  { to: "/stories", label: "Story Bank", icon: BookOpen },
  { to: "/questions", label: "Questions", icon: ListChecks },
  { to: "/companies", label: "Discovery", icon: Building2 },
  { to: "/profile", label: "Profile", icon: User },
];

function Notifications() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get<{ unread: number; items: Notification[] }>("/notifications?limit=30"),
    refetchInterval: 60_000,
  });
  const readAll = useMutation({
    mutationFn: () => api.post("/notifications/read-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  return (
    <div className="relative">
      <button
        aria-label="Notifications"
        className="relative rounded-md p-2 hover:bg-slate-100 dark:hover:bg-slate-800"
        onClick={() => {
          setOpen(!open);
          if (!open && data?.unread) readAll.mutate();
        }}
      >
        <Bell className="h-5 w-5" />
        {!!data?.unread && (
          <span className="absolute -right-0.5 -top-0.5 rounded-full bg-rose-600 px-1.5 text-[10px] font-semibold text-white">{data.unread}</span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 max-h-[70vh] w-80 overflow-y-auto rounded-xl bg-white p-2 shadow-lg ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
          {!data?.items.length && <div className="p-3 text-sm text-slate-500">No notifications yet.</div>}
          {data?.items.map((n) => (
            <button
              key={n.id}
              className="block w-full rounded-md p-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800"
              onClick={() => {
                setOpen(false);
                if (n.link) nav(n.link);
              }}
            >
              <div className={cx("text-sm", !n.read && "font-semibold")}>{n.title}</div>
              {n.body && <div className="line-clamp-2 text-xs text-slate-500">{n.body}</div>}
              <div className="text-[11px] text-slate-400">{fmtDate(n.created_at, true)}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [menu, setMenu] = useState(false);
  const { data: status } = useQuery({ queryKey: ["status"], queryFn: () => api.get<Status>("/status"), refetchInterval: 30_000 });
  return (
    <div className="flex min-h-screen">
      <aside
        className={cx(
          "fixed inset-y-0 left-0 z-30 w-56 shrink-0 border-r border-slate-200 bg-white p-3 transition-transform dark:border-slate-800 dark:bg-slate-900 md:static md:translate-x-0",
          menu ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="mb-6 flex items-center gap-2 px-2 pt-1">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="text-lg font-semibold tracking-tight">JobPilot</span>
        </div>
        <nav className="space-y-0.5">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={() => setMenu(false)}
              className={({ isActive }) =>
                cx(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium",
                  isActive ? "bg-brand-50 text-brand-700 dark:bg-slate-800 dark:text-sky-300" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
                )
              }
            >
              <Icon className="h-4 w-4" /> {label}
            </NavLink>
          ))}
        </nav>
        {status && (
          <div className="mt-8 space-y-1 px-2 text-[11px] text-slate-500">
            <div>
              LLM:{" "}
              {status.llm.configured ? <span className="text-emerald-600">{status.llm.provider} · {status.llm.model_default}</span> : <span className="text-amber-600">no API key</span>}
            </div>
            <div>Spend 24h: ${status.llm.spent_24h_usd.toFixed(2)} / ${status.llm.daily_budget_usd.toFixed(0)}</div>
            <div>Next discovery: {status.discovery.next_run ? fmtDate(status.discovery.next_run, true) : "—"}</div>
          </div>
        )}
      </aside>
      {menu && <div className="fixed inset-0 z-20 bg-black/30 md:hidden" onClick={() => setMenu(false)} />}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-slate-200 bg-white/80 px-4 py-2 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
          <button aria-label="Menu" className="rounded p-2 md:hidden" onClick={() => setMenu(true)}>
            <Menu className="h-5 w-5" />
          </button>
          <div className="text-xs text-slate-500">
            {status?.discovery.running && <span className="animate-pulse text-brand-600">Discovery running…</span>}
          </div>
          <Notifications />
        </header>
        {status && !status.llm.configured && (
          <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            No LLM API key set — fit scores are keyword estimates; tailoring, story generation and live interviews need <code>OPENAI_API_KEY</code> in <code>.env</code> (then restart the container).
          </div>
        )}
        <main className="mx-auto w-full max-w-7xl flex-1 p-4 md:p-6">
          <Suspense fallback={<Spinner />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/jobs" element={<Jobs />} />
              <Route path="/jobs/:id" element={<JobDetail />} />
              <Route path="/pipeline" element={<Pipeline />} />
              <Route path="/interview" element={<InterviewHome />} />
              <Route path="/interview/:id" element={<InterviewSession />} />
              <Route path="/stories" element={<Stories />} />
              <Route path="/questions" element={<Questions />} />
              <Route path="/companies" element={<Companies />} />
              <Route path="/profile" element={<Profile />} />
              <Route path="*" element={<div className="text-sm text-slate-500">Page not found.</div>} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </div>
  );
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  BookOpen,
  Bot,
  CalendarClock,
  Clapperboard,
  Flame,
  Hourglass,
  LayoutDashboard,
  ListTree,
  LogOut,
  Menu,
  Moon,
  Pause,
  Plus,
  Search,
  Settings,
  Sun,
  Target,
  Users,
  Wallet,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from "react-router";
import { api } from "../lib/api";
import { hm, longDate } from "../lib/format";
import { useDebounced, useTheme, useToday } from "../lib/hooks";
import type { Dashboard, SearchHit } from "../lib/types";
import { CaptureModal, Elapsed, sessionSince, useTimer } from "./domain";
import { IconButton, Kbd } from "./ui";

const NAV: { to: string; label: string; icon: ReactNode; end?: boolean }[] = [
  { to: "/", label: "Today", icon: <LayoutDashboard size={18} />, end: true },
  { to: "/life", label: "Life", icon: <Hourglass size={18} /> },
  { to: "/ledger", label: "Ledger", icon: <ListTree size={18} /> },
  { to: "/plan", label: "Planner", icon: <CalendarClock size={18} /> },
  { to: "/journal", label: "Journal", icon: <BookOpen size={18} /> },
  { to: "/assistant", label: "Assistant", icon: <Bot size={18} /> },
  { to: "/goals", label: "Goals", icon: <Target size={18} /> },
  { to: "/habits", label: "Habits", icon: <Flame size={18} /> },
  { to: "/media", label: "Watching", icon: <Clapperboard size={18} /> },
  { to: "/money", label: "Money", icon: <Wallet size={18} /> },
  { to: "/people", label: "People", icon: <Users size={18} /> },
  { to: "/settings", label: "Settings", icon: <Settings size={18} /> },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <div className="relative h-8 w-8">
        <motion.span
          className="absolute inset-0 rounded-full border-2"
          style={{ borderColor: "var(--accent)" }}
          animate={{ rotate: 360 }}
          transition={{ duration: 24, repeat: Infinity, ease: "linear" }}
        >
          <span className="absolute -right-1 top-1/2 h-2 w-2 -translate-y-1/2 rounded-full bg-accent shadow-[0_0_10px_var(--accent)]" />
        </motion.span>
        <span className="absolute inset-[11px] rounded-full bg-amber shadow-[0_0_12px_var(--amber)]" />
      </div>
      <span className="text-[17px] font-semibold tracking-tight">
        Own<span className="text-accent">Life</span>
      </span>
    </div>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex h-full flex-col gap-1 p-3" aria-label="Main">
      <div className="mb-5 mt-1">
        <Logo />
      </div>
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={onNavigate}
          className={({ isActive }) =>
            clsx(
              "group relative flex items-center gap-3 rounded-xl px-3 py-2 text-[14px] font-medium transition-colors",
              isActive ? "text-ink" : "text-ink-3 hover:text-ink-2",
            )
          }
        >
          {({ isActive }) => (
            <>
              {isActive && (
                <motion.span
                  layoutId="nav-active"
                  className="absolute inset-0 rounded-xl bg-panel-hover"
                  transition={{ type: "spring", stiffness: 420, damping: 34 }}
                >
                  <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-accent shadow-[0_0_10px_var(--accent)]" />
                </motion.span>
              )}
              <span className="relative">{item.icon}</span>
              <span className="relative">{item.label}</span>
            </>
          )}
        </NavLink>
      ))}
      <div className="flex-1" />
      <p className="px-3 pb-1 text-[11px] leading-relaxed text-ink-3">“Time is the most important wealth there is.”</p>
    </nav>
  );
}

function TimerPill() {
  const { data, dataUpdatedAt } = useTimer();
  const navigate = useNavigate();
  const timer = data?.running;
  const paused = data?.paused[0];
  if (!timer && !paused) return null;
  return (
    <motion.button
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      onClick={() => navigate("/")}
      className="glass hidden items-center gap-2 rounded-xl px-3 py-1.5 text-[13px] sm:flex"
      title={timer ? "Running timer" : "Paused timer: resume it from Today"}
    >
      {timer ? (
        <>
          <span className="h-2 w-2 animate-pulse-soft rounded-full bg-amber" />
          <span className="max-w-[160px] truncate text-ink-2">{timer.title}</span>
          <span className="font-semibold text-ink">
            <Elapsed since={sessionSince(timer, dataUpdatedAt)} />
          </span>
        </>
      ) : (
        <>
          <Pause size={12} className="text-ink-3" />
          <span className="max-w-[160px] truncate text-ink-3">{paused!.title}</span>
          <span className="num text-ink-3">{hm(paused!.session_seconds)}</span>
        </>
      )}
    </motion.button>
  );
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const debounced = useDebounced(q, 250);
  const search = useQuery({
    queryKey: ["search", debounced],
    queryFn: () => api.get<{ hits: SearchHit[]; mode: string }>(`/api/search?q=${encodeURIComponent(debounced)}&k=6`),
    enabled: open && debounced.trim().length >= 2,
  });
  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
    }
  }, [open]);
  const pages = NAV.filter((n) => n.label.toLowerCase().includes(q.toLowerCase()));
  const hits = search.data?.hits ?? [];
  const items = [
    ...pages.map((p) => ({ key: p.to, label: p.label, hint: "Go to", go: () => navigate(p.to) })),
    ...hits.map((h) => ({
      key: `h${h.chunk_id}`,
      label: h.day_number ? `Day ${h.day_number} — ${h.text.slice(0, 80)}` : `${h.title} — ${h.text.slice(0, 80)}`,
      hint: h.via.join(" + "),
      go: () => navigate(h.source_type === "journal" ? `/journal?entry=${h.source_id}` : `/journal?note=${h.source_id}`),
    })),
  ];
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-[12vh] backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onMouseDown={(e) => e.target === e.currentTarget && onClose()}
        >
          <motion.div
            initial={{ y: -10, opacity: 0, scale: 0.98 }}
            animate={{ y: 0, opacity: 1, scale: 1 }}
            exit={{ y: -6, opacity: 0 }}
            className="glass-solid w-full max-w-xl overflow-hidden rounded-2xl shadow-2xl"
          >
            <div className="flex items-center gap-3 border-b border-line px-4">
              <Search size={18} className="text-ink-3" />
              <input
                autoFocus
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setActive(0);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") onClose();
                  if (e.key === "ArrowDown") setActive((a) => Math.min(items.length - 1, a + 1));
                  if (e.key === "ArrowUp") setActive((a) => Math.max(0, a - 1));
                  if (e.key === "Enter" && items[active]) {
                    items[active].go();
                    onClose();
                  }
                }}
                placeholder="Go to a page, or search your memory…"
                className="h-14 flex-1 bg-transparent text-[15px] text-ink outline-none placeholder:text-ink-3"
              />
              <Kbd>Esc</Kbd>
            </div>
            <ul className="scroll-thin max-h-[50vh] overflow-y-auto p-2">
              {items.map((it, i) => (
                <li key={it.key}>
                  <button
                    onMouseEnter={() => setActive(i)}
                    onClick={() => {
                      it.go();
                      onClose();
                    }}
                    className={clsx(
                      "flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-left text-sm",
                      i === active ? "bg-panel-hover text-ink" : "text-ink-2",
                    )}
                  >
                    <span className="truncate">{it.label}</span>
                    <span className="shrink-0 text-[11px] text-ink-3">{it.hint}</span>
                  </button>
                </li>
              ))}
              {search.isFetching && <li className="px-3 py-2 text-[13px] text-ink-3">Searching…</li>}
              {!items.length && !search.isFetching && <li className="px-3 py-6 text-center text-[13px] text-ink-3">Nothing found.</li>}
            </ul>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function AppShell({ displayName }: { displayName: string }) {
  const [theme, toggleTheme] = useTheme();
  const [mobileNav, setMobileNav] = useState(false);
  const [palette, setPalette] = useState(false);
  const [capture, setCapture] = useState(false);
  const location = useLocation();
  const today = useToday();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/api/dashboard"), staleTime: 30_000 });
  const logout = useMutation({
    mutationFn: () => api.post("/api/auth/logout"),
    onSuccess: () => {
      qc.clear();
      navigate("/login");
      window.location.reload();
    },
  });

  // The evening reminder links to /?capture=1: open Capture, then tidy the URL.
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    if (params.get("capture")) {
      setCapture(true);
      const next = new URLSearchParams(params);
      next.delete("capture");
      setParams(next, { replace: true });
    }
  }, [params, setParams]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(true);
      }
      if (e.altKey && e.key.toLowerCase() === "n") {
        e.preventDefault();
        setCapture(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const subtitle = useMemo(() => {
    const d = dash.data;
    const parts: string[] = [];
    if (d?.awakening_day) parts.push(`Day ${d.awakening_day} of the Virtual Memory`);
    if (d?.life.weeks_alive !== undefined) parts.push(`week ${d.life.weeks_alive.toLocaleString("en")} of ${d.life.total_weeks?.toLocaleString("en")}`);
    return parts.join(" · ");
  }, [dash.data]);

  return (
    <div className="min-h-dvh">
      <div className="app-backdrop" aria-hidden />
      <aside className="glass fixed inset-y-3 left-3 z-30 hidden w-[230px] rounded-2xl lg:block">
        <Sidebar />
      </aside>
      <AnimatePresence>
        {mobileNav && (
          <>
            <motion.div className="fixed inset-0 z-40 bg-black/50 lg:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setMobileNav(false)} />
            <motion.aside
              className="glass-solid fixed inset-y-0 left-0 z-50 w-[260px] lg:hidden"
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: "spring", stiffness: 380, damping: 36 }}
            >
              <div className="absolute right-2 top-3">
                <IconButton label="Close menu" onClick={() => setMobileNav(false)}>
                  <X size={18} />
                </IconButton>
              </div>
              <Sidebar onNavigate={() => setMobileNav(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <div className="lg:pl-[250px]">
        <header className="sticky top-0 z-20 px-4 pt-3 sm:px-6">
          <div className="glass-strong flex h-14 items-center gap-3 rounded-2xl px-3 shadow-lg shadow-black/20 sm:px-4">
            <IconButton label="Menu" className="lg:hidden" onClick={() => setMobileNav(true)}>
              <Menu size={18} />
            </IconButton>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[14px] font-semibold text-ink">{longDate(today)}</p>
              {subtitle && <p className="truncate text-[12px] text-ink-3">{subtitle}</p>}
            </div>
            <TimerPill />
            <button
              onClick={() => setPalette(true)}
              className="hidden h-9 items-center gap-2 rounded-xl border border-line px-3 text-[13px] text-ink-3 transition hover:text-ink-2 md:flex"
            >
              <Search size={15} /> Search <Kbd>Ctrl K</Kbd>
            </button>
            <IconButton label="Search" className="md:hidden" onClick={() => setPalette(true)}>
              <Search size={18} />
            </IconButton>
            <button
              onClick={() => setCapture(true)}
              className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-accent px-3 text-[13px] font-semibold text-accent-ink shadow-[0_0_20px_-6px_var(--accent)] transition hover:brightness-110"
              title="Capture (Alt+N)"
            >
              <Plus size={16} /> <span className="hidden sm:inline">Capture</span>
            </button>
            <IconButton label={theme === "dark" ? "Light theme" : "Dark theme"} onClick={toggleTheme}>
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </IconButton>
            <IconButton label={`Log out ${displayName}`} onClick={() => logout.mutate()}>
              <LogOut size={17} />
            </IconButton>
          </div>
        </header>

        <main className="px-4 pb-16 pt-6 sm:px-6">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
              className="mx-auto max-w-[1400px]"
            >
              <Outlet context={{ openCapture: () => setCapture(true) }} />
            </motion.div>
          </AnimatePresence>
        </main>
      </div>

      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <CaptureModal open={capture} onClose={() => setCapture(false)} />
    </div>
  );
}

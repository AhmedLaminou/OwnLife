import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { Navigate, RouterProvider, createBrowserRouter } from "react-router";
import { AppShell } from "./components/AppShell";
import { Spinner } from "./components/ui";
import { api } from "./lib/api";
import type { AuthStatus } from "./lib/types";
import { AssistantPage } from "./pages/Assistant";
import { AuthPage } from "./pages/Auth";
import { GoalsPage } from "./pages/Goals";
import { HabitsPage } from "./pages/Habits";
import { JournalPage } from "./pages/Journal";
import { LedgerPage } from "./pages/Ledger";
import { LifePage } from "./pages/Life";
import { MediaPage } from "./pages/Media";
import { MoneyPage } from "./pages/Money";
import { PeoplePage } from "./pages/People";
import { PlannerPage } from "./pages/Planner";
import { SettingsPage } from "./pages/Settings";
import { TodayPage } from "./pages/Today";

function Gate() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["auth"],
    queryFn: () => api.get<AuthStatus>("/api/auth/status"),
    staleTime: 60_000,
  });
  if (isLoading) {
    return (
      <div className="grid min-h-dvh place-items-center">
        <div className="app-backdrop" aria-hidden />
        <Spinner />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="grid min-h-dvh place-items-center p-6 text-center">
        <div className="app-backdrop" aria-hidden />
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="glass max-w-md rounded-2xl p-6">
          <p className="text-lg font-semibold">The OwnLife server is not answering</p>
          <p className="mt-2 text-sm text-ink-3">
            Start it from <code>backend/</code> with <code>.venv\Scripts\python.exe -m app</code>, then reload this page.
          </p>
        </motion.div>
      </div>
    );
  }
  if (data.needs_setup) return <AuthPage mode="setup" />;
  if (!data.authenticated || !data.user) return <AuthPage mode="login" />;
  return <AppShell displayName={data.user.display_name} />;
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <Gate />,
    children: [
      { index: true, element: <TodayPage /> },
      { path: "life", element: <LifePage /> },
      { path: "ledger", element: <LedgerPage /> },
      { path: "plan", element: <PlannerPage /> },
      { path: "journal", element: <JournalPage /> },
      { path: "assistant", element: <AssistantPage /> },
      { path: "goals", element: <GoalsPage /> },
      { path: "habits", element: <HabitsPage /> },
      { path: "media", element: <MediaPage /> },
      { path: "money", element: <MoneyPage /> },
      { path: "people", element: <PeoplePage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "login", element: <Navigate to="/" replace /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}

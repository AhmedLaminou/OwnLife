import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, LockKeyhole } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { Button, ErrorNote, Field, Input } from "../components/ui";
import { api } from "../lib/api";

const ZONES = ["Africa/Niamey", "Africa/Lagos", "Africa/Tunis", "Africa/Casablanca", "Europe/Paris", "UTC"];

export function AuthPage({ mode }: { mode: "setup" | "login" }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    email: "",
    password: "",
    display_name: "",
    birth_date: "",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Africa/Niamey",
  });
  const submit = useMutation({
    mutationFn: () =>
      mode === "setup"
        ? api.post("/api/auth/setup", { ...form, birth_date: form.birth_date || null })
        : api.post("/api/auth/login", { email: form.email, password: form.password }),
    onSuccess: () => qc.invalidateQueries(),
  });

  return (
    <div className="grid min-h-dvh place-items-center p-4">
      <div className="app-backdrop" aria-hidden />
      <motion.div
        initial={{ opacity: 0, y: 14, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="glass w-full max-w-md rounded-3xl p-8"
      >
        <div className="mb-7 flex flex-col items-center text-center">
          <div className="relative mb-4 h-14 w-14">
            <motion.span
              className="absolute inset-0 rounded-full border-2"
              style={{ borderColor: "var(--accent)" }}
              animate={{ rotate: 360 }}
              transition={{ duration: 18, repeat: Infinity, ease: "linear" }}
            >
              <span className="absolute -right-1.5 top-1/2 h-3 w-3 -translate-y-1/2 rounded-full bg-accent shadow-[0_0_14px_var(--accent)]" />
            </motion.span>
            <span className="absolute inset-[19px] rounded-full bg-amber shadow-[0_0_18px_var(--amber)]" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {mode === "setup" ? "Begin your record" : "Welcome back"}
          </h1>
          <p className="mt-1.5 text-sm text-ink-3">
            {mode === "setup"
              ? "OwnLife keeps everything on this machine. Create the account that guards it."
              : "Your life, your data, this machine."}
          </p>
        </div>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit.mutate();
          }}
        >
          {mode === "setup" && (
            <Field label="Your name">
              <Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} required autoFocus />
            </Field>
          )}
          <Field label="Email">
            <Input type="email" autoComplete="username" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required autoFocus={mode === "login"} />
          </Field>
          <Field label="Password" hint={mode === "setup" ? "At least 10 characters. It cannot be recovered by anyone but you (python -m app.cli reset-password)." : undefined}>
            <Input
              type="password"
              autoComplete={mode === "setup" ? "new-password" : "current-password"}
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              minLength={mode === "setup" ? 10 : undefined}
              required
            />
          </Field>
          {mode === "setup" && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Birth date">
                <Input type="date" value={form.birth_date} onChange={(e) => setForm({ ...form, birth_date: e.target.value })} />
              </Field>
              <Field label="Time zone">
                <Input list="zones" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
                <datalist id="zones">
                  {ZONES.map((z) => (
                    <option key={z} value={z} />
                  ))}
                </datalist>
              </Field>
            </div>
          )}
          <ErrorNote error={submit.error} />
          <Button type="submit" variant="primary" size="lg" className="w-full" loading={submit.isPending} icon={mode === "setup" ? <ArrowRight size={17} /> : <LockKeyhole size={16} />}>
            {mode === "setup" ? "Create account" : "Log in"}
          </Button>
        </form>
      </motion.div>
    </div>
  );
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity,
  Archive,
  BellRing,
  CheckCircle2,
  CircleAlert,
  Copy,
  Database,
  Download,
  FileText,
  KeyRound,
  MonitorPlay,
  MoonStar,
  Plus,
  RefreshCw,
  Sprout,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { CategorySelect, KindDot, useInvalidateLedger } from "../components/domain";
import { Badge, Button, Card, ErrorNote, Field, Input, PageHeader, Select, Spinner, Tabs, Textarea, Toggle, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useCategories, useProfile, useToday } from "../lib/hooks";
import { CHART_KINDS, KIND_HINT, KIND_LABEL } from "../lib/kinds";
import type {
  AiStatus,
  ApiToken,
  BackupMirror,
  Category,
  GlossaryItem,
  MeasuredToday,
  PrayerDay,
  PrayerName,
  PrayerPrefs,
  Prefs,
  Profile,
  ReminderPrefs,
  RemindersStatus,
  Rule,
  SyncStatus,
} from "../lib/types";

type Tab = "profile" | "rituals" | "privacy" | "ai" | "categories" | "integrations" | "data";

function Status({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <p className="flex items-center gap-2 text-sm">
      {ok ? <CheckCircle2 size={16} className="text-good" /> : <CircleAlert size={16} className="text-warning" />}
      <span className={ok ? "text-ink-2" : "text-ink"}>{children}</span>
    </p>
  );
}

function ProfileTab({ profile }: { profile: Profile }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState(profile);
  useEffect(() => setF(profile), [profile]);
  const save = useMutation({
    mutationFn: () =>
      api.put<Profile>("/api/profile", {
        display_name: f.display_name,
        birth_date: f.birth_date || null,
        life_expectancy_years: f.life_expectancy_years,
        timezone: f.timezone,
        currency: f.currency,
        awakening_date: f.awakening_date || null,
        mission_title: f.mission_title || null,
        mission_text: f.mission_text || null,
        focus_target_hours: f.focus_target_hours,
        stretch_focus_hours: f.stretch_focus_hours,
        sleep_target_hours: f.sleep_target_hours,
        noise_budget_hours: f.noise_budget_hours,
        wake_target: f.wake_target || null,
        bed_target: f.bed_target || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries();
      toast("Profile saved", "good");
    },
  });
  const num = (k: keyof Profile) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: Number(e.target.value) });
  return (
    <Card title="Profile" subtitle="The parameters of the life the app measures">
      <form className="grid gap-4 md:grid-cols-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Name"><Input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
        <Field label="Birth date"><Input type="date" value={f.birth_date ?? ""} onChange={(e) => setF({ ...f, birth_date: e.target.value })} /></Field>
        <Field label="Horizon (years)" hint="90 is the usual assumption"><Input type="number" min={30} max={150} value={f.life_expectancy_years} onChange={num("life_expectancy_years")} /></Field>
        <Field label="Time zone"><Input value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></Field>
        <Field label="Currency"><Input value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })} /></Field>
        <Field label="The Awakening" hint="Day 1 of the Virtual Memory"><Input type="date" value={f.awakening_date ?? ""} onChange={(e) => setF({ ...f, awakening_date: e.target.value })} /></Field>
        <Field label="Mission title"><Input value={f.mission_title ?? ""} onChange={(e) => setF({ ...f, mission_title: e.target.value })} /></Field>
        <Field label="Mission" className="md:col-span-2"><Textarea rows={2} value={f.mission_text ?? ""} onChange={(e) => setF({ ...f, mission_text: e.target.value })} /></Field>
        <Field label="Core work target (h/day)"><Input type="number" step={0.5} value={f.focus_target_hours} onChange={num("focus_target_hours")} /></Field>
        <Field label="Stretch ([Sprint]) target"><Input type="number" step={0.5} value={f.stretch_focus_hours} onChange={num("stretch_focus_hours")} /></Field>
        <Field label="Sleep (h/night)"><Input type="number" step={0.5} value={f.sleep_target_hours} onChange={num("sleep_target_hours")} /></Field>
        <Field label="Noise budget (h/day)"><Input type="number" step={0.25} value={f.noise_budget_hours} onChange={num("noise_budget_hours")} /></Field>
        <Field label="Wake at"><Input type="time" value={f.wake_target ?? ""} onChange={(e) => setF({ ...f, wake_target: e.target.value })} /></Field>
        <Field label="Bed at"><Input type="time" value={f.bed_target ?? ""} onChange={(e) => setF({ ...f, bed_target: e.target.value })} /></Field>
        <div className="flex items-end justify-end md:col-span-3">
          <ErrorNote error={save.error} />
          <Button type="submit" variant="primary" loading={save.isPending}>Save</Button>
        </div>
      </form>
    </Card>
  );
}

function PrivacyTab({ profile }: { profile: Profile }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [glossary, setGlossary] = useState<GlossaryItem[]>(profile.glossary);
  const [redactions, setRedactions] = useState(profile.redactions);
  const save = useMutation({
    mutationFn: () => api.put("/api/profile", { glossary: glossary.filter((g) => g.term.trim()), redactions: redactions.filter((r) => r.pattern.trim()) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profile"] });
      toast("Saved", "good");
    },
  });
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Card title="Your vocabulary" subtitle="The assistant uses these terms. Private ones: alias only, the meaning never leaves this machine.">
        <div className="space-y-2">
          {glossary.map((g, i) => (
            <div key={i} className="grid grid-cols-[150px_1fr_auto_auto] items-center gap-2">
              <Input value={g.term} onChange={(e) => setGlossary(glossary.map((x, j) => (j === i ? { ...x, term: e.target.value } : x)))} />
              <Input value={g.meaning} onChange={(e) => setGlossary(glossary.map((x, j) => (j === i ? { ...x, meaning: e.target.value } : x)))} className={g.private ? "private-blur" : undefined} />
              <Toggle checked={g.private} onChange={(v) => setGlossary(glossary.map((x, j) => (j === i ? { ...x, private: v } : x)))} label="" />
              <button onClick={() => setGlossary(glossary.filter((_, j) => j !== i))} className="text-ink-3 hover:text-critical" aria-label="Remove"><Trash2 size={15} /></button>
            </div>
          ))}
          <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => setGlossary([...glossary, { term: "", meaning: "", private: false }])}>Term</Button>
        </div>
      </Card>
      <Card title="Redactions" subtitle="Applied to any text sent to a cloud model (regular expressions, case-insensitive)">
        <div className="space-y-2">
          {redactions.map((r, i) => (
            <div key={i} className="grid grid-cols-[1fr_160px_auto] items-center gap-2">
              <Input value={r.pattern} onChange={(e) => setRedactions(redactions.map((x, j) => (j === i ? { ...x, pattern: e.target.value } : x)))} className="private-blur font-mono text-[13px]" />
              <Input value={r.replace} onChange={(e) => setRedactions(redactions.map((x, j) => (j === i ? { ...x, replace: e.target.value } : x)))} />
              <button onClick={() => setRedactions(redactions.filter((_, j) => j !== i))} className="text-ink-3 hover:text-critical" aria-label="Remove"><Trash2 size={15} /></button>
            </div>
          ))}
          <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => setRedactions([...redactions, { pattern: "", replace: "[private]" }])}>Redaction</Button>
        </div>
        <div className="mt-5 rounded-xl border border-line p-3 text-[13px] leading-relaxed text-ink-3">
          What leaves the machine: only the passages relevant to a question, the day's numbers, and your message — after these redactions. Journal pages and entries marked private are never sent.
          Embeddings and search always run locally.
        </div>
      </Card>
      <div className="xl:col-span-2 flex justify-end">
        <ErrorNote error={save.error} />
        <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending}>Save vocabulary and redactions</Button>
      </div>
    </div>
  );
}

function AiTab({ profile }: { profile: Profile }) {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ["ai-status"], queryFn: () => api.get<AiStatus>("/api/ai/status") });
  const [mode, setMode] = useState(profile.ai_settings.mode ?? "");
  const [models, setModels] = useState((profile.ai_settings.models ?? []).join("\n"));
  const save = useMutation({
    mutationFn: () =>
      api.put("/api/profile", { ai_settings: { mode: mode || null, models: models.trim() ? models.split(/\s*[\n,]\s*/).filter(Boolean) : null } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profile"] });
      qc.invalidateQueries({ queryKey: ["ai-status"] });
      toast("AI settings saved", "good");
    },
  });
  const s = status.data;
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Card title="Mode and models">
        <div className="space-y-4">
          <Field label="Mode">
            <Select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="">Server default (backend/.env)</option>
              <option value="cloud_then_local">Free cloud models, then local Ollama when offline</option>
              <option value="cloud">Free cloud models only</option>
              <option value="local">Local only — private, offline, slower</option>
              <option value="off">Off</option>
            </Select>
          </Field>
          <Field label="OpenRouter models (one per line, in fallback order)" hint="Leave empty to use OPENROUTER_MODELS from backend/.env">
            <Textarea rows={4} value={models} onChange={(e) => setModels(e.target.value)} className="font-mono text-[13px]" placeholder={s?.openrouter_models.join("\n")} />
          </Field>
          <ErrorNote error={save.error} />
          <div className="flex justify-end"><Button variant="primary" onClick={() => save.mutate()} loading={save.isPending}>Save</Button></div>
        </div>
      </Card>
      <Card title="Status" action={<Button size="sm" variant="ghost" icon={<RefreshCw size={14} />} onClick={() => status.refetch()}>Check</Button>}>
        {!s ? (
          <Spinner />
        ) : (
          <div className="space-y-2.5">
            <Status ok={s.chain.length > 0}>{s.chain.length ? `Chain: ${s.chain.join(" → ")}` : "No model available in this mode"}</Status>
            <Status ok={s.has_openrouter_key}>{s.has_openrouter_key ? "OpenRouter key present (not shown)" : "No OPENROUTER_API_KEY in backend/.env"}</Status>
            <Status ok={s.ollama.reachable}>{s.ollama.reachable ? "Ollama is running" : "Ollama is not running"}</Status>
            <Status ok={s.ollama.embed_model}>Embeddings model {s.ollama.embed_model_name}{s.ollama.embed_model ? "" : " — not pulled"}</Status>
            <Status ok={s.ollama.chat_model}>Offline chat model {s.ollama.chat_model_name}{s.ollama.chat_model ? "" : " — not pulled"}</Status>
            <p className="pt-2 text-[12px] leading-relaxed text-ink-3">{s.free_tier} Free endpoints may log prompts: that is the price of free, which is why redactions exist and why local mode is one click away.</p>
          </div>
        )}
      </Card>
    </div>
  );
}

function CategoriesTab() {
  const qc = useQueryClient();
  const invalidate = useInvalidateLedger();
  const toast = useToast();
  const { data: cats } = useCategories(true);
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api.get<Rule[]>("/api/rules") });
  const [newCat, setNewCat] = useState({ name: "", kind: "core" });
  const [newRule, setNewRule] = useState({ field: "channel", pattern: "", category_id: null as number | null, is_regex: false });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["categories"] });
    qc.invalidateQueries({ queryKey: ["rules"] });
  };
  const patchCat = useMutation({ mutationFn: ({ id, ...b }: Partial<Category> & { id: number }) => api.patch(`/api/categories/${id}`, b), onSuccess: refresh });
  const addCat = useMutation({ mutationFn: () => api.post("/api/categories", newCat), onSuccess: () => { refresh(); setNewCat({ name: "", kind: "core" }); } });
  const addRule = useMutation({
    mutationFn: () => api.post("/api/rules", newRule),
    onSuccess: () => { refresh(); setNewRule({ ...newRule, pattern: "" }); },
  });
  const delRule = useMutation({ mutationFn: (id: number) => api.del(`/api/rules/${id}`), onSuccess: refresh });
  const reapply = useMutation({
    mutationFn: () => api.post<{ activitywatch_reclassified: number; youtube_blocks_rebuilt: number }>("/api/rules/reapply"),
    onSuccess: (r) => { invalidate(); toast(`${r.activitywatch_reclassified} entries reclassified, ${r.youtube_blocks_rebuilt} YouTube blocks rebuilt`, "good"); },
  });
  const catName = (id: number) => cats?.find((c) => c.id === id)?.name ?? "?";
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Card title="Categories" subtitle="The kind decides signal vs noise in every chart">
        <ul className="divide-y divide-line">
          {cats?.map((c) => (
            <li key={c.id} className={clsx("flex items-center gap-2 py-2", c.archived && "opacity-50")}>
              <KindDot kind={c.kind} />
              <Input defaultValue={c.name} onBlur={(e) => e.target.value !== c.name && patchCat.mutate({ id: c.id, name: e.target.value })} className="h-8 flex-1" />
              <Select value={c.kind} onChange={(e) => patchCat.mutate({ id: c.id, kind: e.target.value as Category["kind"] })} className="h-8 w-36">
                {[...CHART_KINDS, "destructive"].map((k) => <option key={k} value={k} title={KIND_HINT[k as keyof typeof KIND_HINT]}>{k === "destructive" ? "Quitting" : KIND_LABEL[k as keyof typeof KIND_LABEL]}</option>)}
              </Select>
              <button onClick={() => patchCat.mutate({ id: c.id, archived: !c.archived })} className="text-ink-3 hover:text-ink" title={c.archived ? "Restore" : "Archive"}><Archive size={15} /></button>
            </li>
          ))}
        </ul>
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); addCat.mutate(); }}>
          <Input value={newCat.name} onChange={(e) => setNewCat({ ...newCat, name: e.target.value })} placeholder="New category" required />
          <Select value={newCat.kind} onChange={(e) => setNewCat({ ...newCat, kind: e.target.value })} className="w-40">
            {CHART_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
          </Select>
          <Button type="submit" icon={<Plus size={15} />}>Add</Button>
        </form>
        <ErrorNote error={addCat.error} />
      </Card>
      <Card
        title="Classification rules"
        subtitle="For YouTube history and ActivityWatch. First match wins (lowest priority number)."
        action={<Button size="sm" icon={<RefreshCw size={14} />} onClick={() => reapply.mutate()} loading={reapply.isPending}>Re-apply</Button>}
      >
        <ul className="scroll-thin max-h-[440px] divide-y divide-line overflow-y-auto">
          {rules.data?.map((r) => (
            <li key={r.id} className="flex items-center gap-2 py-2 text-[13px]">
              <Badge>{r.field}</Badge>
              <code className="min-w-0 flex-1 truncate text-ink">{r.pattern}</code>
              {r.is_regex && <Badge tone="amber">regex</Badge>}
              <span className="text-ink-3">→ {catName(r.category_id)}</span>
              <button onClick={() => delRule.mutate(r.id)} className="text-ink-3 hover:text-critical" aria-label="Delete rule"><Trash2 size={14} /></button>
            </li>
          ))}
        </ul>
        <form className="mt-3 grid grid-cols-[110px_1fr] gap-2 sm:grid-cols-[110px_1fr_190px_auto]" onSubmit={(e) => { e.preventDefault(); addRule.mutate(); }}>
          <Select value={newRule.field} onChange={(e) => setNewRule({ ...newRule, field: e.target.value })}>
            {["channel", "title", "domain", "app", "url"].map((f) => <option key={f}>{f}</option>)}
          </Select>
          <Input value={newRule.pattern} onChange={(e) => setNewRule({ ...newRule, pattern: e.target.value })} placeholder="ReactionHub" required />
          <CategorySelect value={newRule.category_id} onChange={(v) => setNewRule({ ...newRule, category_id: v })} allowNone={false} />
          <Button type="submit" icon={<Plus size={15} />} disabled={!newRule.category_id}>Rule</Button>
        </form>
        <ErrorNote error={addRule.error} />
      </Card>
    </div>
  );
}

const PRAYER_LABEL: Record<PrayerName, string> = {
  fajr: "Fajr",
  sunrise: "Sunrise",
  dhuhr: "Dhuhr",
  asr: "Asr",
  maghrib: "Maghrib",
  isha: "Isha",
};

function RemindersCard({ prefs }: { prefs: Prefs }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: profile } = useProfile();
  const [f, setF] = useState<ReminderPrefs>(prefs.reminders);
  useEffect(() => setF(prefs.reminders), [prefs.reminders]);
  const status = useQuery({ queryKey: ["reminders"], queryFn: () => api.get<RemindersStatus>("/api/reminders") });
  const save = useMutation({
    mutationFn: () => api.put("/api/prefs/reminders", f),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["prefs"] });
      qc.invalidateQueries({ queryKey: ["reminders"] });
      toast("Reminders saved", "good");
    },
  });
  const test = useMutation({
    mutationFn: () => api.post<{ shown: boolean; detail: string }>("/api/reminders/test"),
    onSuccess: (r) => toast(r.shown ? "Sent — look at the corner of the screen" : `Not shown: ${r.detail}`, r.shown ? "good" : "critical"),
  });
  const bed = profile?.bed_target ?? "23:00";
  const wake = profile?.wake_target ?? "06:00";
  const LABEL = { capture: "Capture the day", bedtime: "Bedtime", morning: "Morning summary" } as const;
  return (
    <Card
      title="Evening ritual and morning summary"
      subtitle={`Windows notifications from OwnLife, from your bed time (${bed}) and wake time (${wake}) — Settings → Profile`}
      action={<BellRing size={18} className="text-ink-3" />}
    >
      <div className="space-y-3">
        <Toggle checked={f.enabled} onChange={(v) => setF({ ...f, enabled: v })} label="Send reminders" />
        <div className={clsx("space-y-3 pl-1", !f.enabled && "pointer-events-none opacity-50")}>
          <div className="flex flex-wrap items-center gap-2">
            <Toggle checked={f.capture} onChange={(v) => setF({ ...f, capture: v })} label="“Capture your day”" />
            <Input type="number" min={5} max={180} value={f.capture_before_bed_minutes}
              onChange={(e) => setF({ ...f, capture_before_bed_minutes: Number(e.target.value) })} className="w-20" aria-label="Minutes before bed time" />
            <span className="text-[13px] text-ink-3">minutes before bed time</span>
          </div>
          <Toggle checked={f.bedtime} onChange={(v) => setF({ ...f, bedtime: v })} label="Bedtime guard, at bed time" />
          {f.bedtime && (
            <Input value={f.bedtime_text} onChange={(e) => setF({ ...f, bedtime_text: e.target.value })} maxLength={200} aria-label="Bedtime message" />
          )}
          <Toggle checked={f.morning} onChange={(v) => setF({ ...f, morning: v })} label="Morning summary at wake time (yesterday, today's prayers)" />
          <Toggle checked={f.auto_review} onChange={(v) => setF({ ...f, auto_review: v })} label="Write yesterday's review each morning (one model request when AI is on)" />
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="text-ink-2">“Still on it?” when a timer runs for</span>
            <Select value={f.timer_nudge_minutes} onChange={(e) => setF({ ...f, timer_nudge_minutes: Number(e.target.value) })} className="w-auto" aria-label="Long timer reminder">
              <option value={0}>never</option>
              <option value={60}>1 hour</option>
              <option value={90}>1 h 30</option>
              <option value={120}>2 hours</option>
              <option value={180}>3 hours</option>
              <option value={240}>4 hours</option>
            </Select>
            <span className="text-ink-3">(then twice, three times as long)</span>
          </div>
        </div>
        <p className="text-[12px] leading-relaxed text-ink-3">
          Notifications come from this computer while OwnLife runs; a reminder missed by more than 45 minutes (laptop off) is skipped.
          The bedtime message is shown on screen: keep it discreet. Clicking a notification opens the right page.
        </p>
        {status.data && status.data.today.length > 0 && (
          <ul className="flex flex-wrap gap-1.5">
            {status.data.today.map((r) => (
              <li key={r.key}>
                <Badge tone={r.sent ? "good" : r.past ? "neutral" : "accent"}>
                  {r.time} {LABEL[r.key]} {r.sent ? "· sent" : r.past ? "· missed" : ""}
                </Badge>
              </li>
            ))}
          </ul>
        )}
        {status.data?.last_error && <ErrorNote error={new Error(status.data.last_error)} />}
        <ErrorNote error={save.error ?? test.error} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" icon={<BellRing size={15} />} onClick={() => test.mutate()} loading={test.isPending} disabled={!prefs.notifications_available}>
            Send a test notification
          </Button>
          <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending}>Save</Button>
        </div>
      </div>
    </Card>
  );
}

function PrayerCard({ prefs }: { prefs: Prefs }) {
  const qc = useQueryClient();
  const toast = useToast();
  const today = useToday();
  const [f, setF] = useState<PrayerPrefs>(prefs.prayer);
  useEffect(() => setF(prefs.prayer), [prefs.prayer]);
  const preview = useQuery({ queryKey: ["prayer", today], queryFn: () => api.get<PrayerDay>(`/api/prayer/${today}`) });
  const save = useMutation({
    mutationFn: () => api.put("/api/prefs/prayer", f),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["prefs"] });
      qc.invalidateQueries({ queryKey: ["prayer"] });
      toast("Prayer times saved", "good");
    },
  });
  const setOffset = (k: PrayerName, v: number) => setF({ ...f, offsets: { ...f.offsets, [k]: v } });
  return (
    <Card title="Prayer times" subtitle="Computed offline from the sun's position — matched against a published Niamey timetable" action={<MoonStar size={18} className="text-ink-3" />}>
      <div className="space-y-4">
        {preview.data && (
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
            {(Object.keys(PRAYER_LABEL) as PrayerName[]).map((k) => (
              <div key={k} className={clsx("rounded-xl border border-line px-2 py-2 text-center", preview.data.next?.name === k && "border-accent")}>
                <p className="text-[11px] uppercase tracking-wider text-ink-3">{PRAYER_LABEL[k]}</p>
                <p className="num text-[15px] font-semibold text-ink">{preview.data.times[k] ?? "—"}</p>
              </div>
            ))}
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="City"><Input value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
          <Field label="Latitude"><Input type="number" step="0.0001" value={f.latitude} onChange={(e) => setF({ ...f, latitude: Number(e.target.value) })} /></Field>
          <Field label="Longitude"><Input type="number" step="0.0001" value={f.longitude} onChange={(e) => setF({ ...f, longitude: Number(e.target.value) })} /></Field>
          <Field label="Method" className="sm:col-span-2">
            <Select value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>
              {prefs.prayer_methods.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </Select>
          </Field>
          <Field label="Asr" hint="Standard: Maliki, Shafi'i, Hanbali">
            <Select value={f.asr} onChange={(e) => setF({ ...f, asr: e.target.value as PrayerPrefs["asr"] })}>
              <option value="standard">Standard</option>
              <option value="hanafi">Hanafi</option>
            </Select>
          </Field>
        </div>
        <div>
          <p className="mb-1.5 text-[12px] font-medium uppercase tracking-wider text-ink-3">Minutes to add, to match your mosque</p>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
            {(Object.keys(PRAYER_LABEL) as PrayerName[]).map((k) => (
              <label key={k} className="text-[12px] text-ink-3">
                {PRAYER_LABEL[k]}
                <Input type="number" min={-60} max={120} value={f.offsets[k] ?? 0} onChange={(e) => setOffset(k, Number(e.target.value))} className="mt-1 h-9 w-full" />
              </label>
            ))}
          </div>
        </div>
        <Toggle checked={f.align_planner} onChange={(v) => setF({ ...f, align_planner: v })} label="When a template is applied, move its prayer blocks to these times" />
        <ErrorNote error={save.error} />
        <div className="flex justify-end">
          <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending}>Save</Button>
        </div>
      </div>
    </Card>
  );
}

function RitualsTab() {
  const prefs = useQuery({ queryKey: ["prefs"], queryFn: () => api.get<Prefs>("/api/prefs") });
  if (!prefs.data) return <Spinner />;
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <RemindersCard prefs={prefs.data} />
      <PrayerCard prefs={prefs.data} />
    </div>
  );
}

function ExtensionCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api.get<ApiToken[]>("/api/tokens") });
  const measured = useQuery({ queryKey: ["measured"], queryFn: () => api.get<MeasuredToday>("/api/ingest/youtube/recent"), refetchInterval: 60_000 });
  const [fresh, setFresh] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api.post<ApiToken>("/api/tokens", { name: "YouTube extension", scopes: ["youtube"] }),
    onSuccess: (t) => {
      setFresh(t.token ?? null);
      qc.invalidateQueries({ queryKey: ["tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: number) => api.del(`/api/tokens/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tokens"] });
      toast("Key revoked: the extension using it stops at once");
    },
  });
  const m = measured.data;
  return (
    <Card title="YouTube extension" subtitle="Real minutes per video, measured in your browser, sent only to this OwnLife" action={<MonitorPlay size={18} className="text-ink-3" />}>
      <div className="space-y-3 text-sm">
        <Status ok={!!m?.last_seen}>
          {m?.last_seen
            ? `Last heartbeat ${new Date(m.last_seen).toLocaleString()} · today ${Math.round((m.today.seconds ?? 0) / 60)} min measured`
            : "No heartbeat yet: install the extension and paste a key"}
        </Status>
        <ol className="list-decimal space-y-1 pl-5 text-[13px] text-ink-2">
          <li>Chrome or Edge → <code>chrome://extensions</code> → turn on <b>Developer mode</b>.</li>
          <li>
            <b>Load unpacked</b> → choose the <code>extension</code> folder of OwnLife. Not “Pack extension”: that makes a
            <code>.crx</code> and a private <code>.pem</code> signing key, neither of which is needed.
          </li>
          <li>Create a key below. In the extension: right-click its icon → <b>Options</b> → paste the key → <b>Save</b>. It answers “Connected”.</li>
          <li>Keep the key nowhere else. OwnLife stores only its fingerprint: if it is lost, revoke it and create another.</li>
        </ol>
        {fresh && (
          <div className="space-y-2 rounded-xl border border-accent/40 bg-accent-soft p-3">
            <p className="text-[13px] text-ink">Your new key — shown once. Copy it into the extension:</p>
            <div className="flex gap-2">
              <Input readOnly value={fresh} className="font-mono text-[12px]" onFocus={(e) => e.target.select()} />
              <Button icon={<Copy size={14} />} onClick={() => navigator.clipboard?.writeText(fresh).then(() => toast("Copied", "good"))}>Copy</Button>
            </div>
          </div>
        )}
        {tokens.data && tokens.data.length > 0 && (
          <ul className="divide-y divide-line">
            {tokens.data.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-2 py-1.5 text-[13px]">
                <span className="text-ink-2">
                  {t.name} <code className="text-ink-3">{t.prefix}…</code>
                </span>
                <span className="flex items-center gap-2 text-ink-3">
                  {t.last_used_at ? `used ${new Date(t.last_used_at).toLocaleDateString()}` : "never used"}
                  <button onClick={() => revoke.mutate(t.id)} className="hover:text-critical" aria-label="Revoke"><Trash2 size={14} /></button>
                </span>
              </li>
            ))}
          </ul>
        )}
        <ErrorNote error={create.error ?? revoke.error} />
        <div className="flex justify-end">
          <Button variant="primary" icon={<KeyRound size={15} />} onClick={() => create.mutate()} loading={create.isPending}>Create a key</Button>
        </div>
        <p className="text-[12px] leading-relaxed text-ink-3">
          A key can only send YouTube minutes — it cannot read your journal or anything else — and can be revoked here at any time.
          Videos watched on your phone are not seen: Google Takeout remains the way to bring those in.
        </p>
      </div>
    </Card>
  );
}

function FilesCard() {
  const sync = useQuery({ queryKey: ["sync-status"], queryFn: () => api.get<SyncStatus>("/api/sync"), refetchInterval: 10_000 });
  const s = sync.data;
  return (
    <Card title="Your Markdown files" subtitle="The journal and the notes beside it, synced both ways" action={<FileText size={18} className="text-ink-3" />}>
      {!s ? (
        <Spinner />
      ) : !s.configured ? (
        <Status ok={false}>Set JOURNAL_SYNC_PATH and IMPORT_ROOT in backend/.env</Status>
      ) : (
        <div className="space-y-2.5 text-sm">
          <Status ok={s.watching && !s.last_error}>
            {s.watching ? "Watching the files — a save reaches OwnLife within seconds" : "Not watching (FILE_SYNC=false): use Sync now on the Journal page"}
          </Status>
          <Status ok={s.writeback}>{s.writeback ? "Edits made in OwnLife are written into the files" : "Read-only: OwnLife never writes your files (FILE_SYNC_WRITEBACK=false)"}</Status>
          {s.files.filter((f) => f.kind === "journal").map((f) => (
            <p key={f.path} className="text-[13px] text-ink-3">{f.path} · {f.days} days</p>
          ))}
          {s.yearly && <p className="text-[13px] text-ink-3">Path pattern: {s.pattern}</p>}
          {(s.conflicts.length > 0 || s.missing.length > 0) && (
            <Status ok={false}>{s.conflicts.length} conflict(s), {s.missing.length} missing — open the Journal's sync panel</Status>
          )}
          {s.last_error && <ErrorNote error={new Error(s.last_error)} />}
          <p className="text-[12px] leading-relaxed text-ink-3">
            Before every write the previous version is copied to {s.history_dir}. A day edited both in the file and here is never
            overwritten: both versions are kept until you choose.
          </p>
        </div>
      )}
    </Card>
  );
}

function SeedCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const seed = useQuery({ queryKey: ["personal-seed"], queryFn: () => api.get<{ exists: boolean; allowed: boolean; path: string }>("/api/system/personal-seed") });
  const apply = useMutation({
    mutationFn: () => api.post<{ applied: Record<string, number> }>("/api/system/personal-seed"),
    onSuccess: (r) => {
      qc.invalidateQueries();
      const parts = Object.entries(r.applied).map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`);
      toast(parts.length ? `Applied: ${parts.join(", ")}` : "Nothing new: already applied", "good");
    },
  });
  if (!seed.data?.exists) return null;
  return (
    <Card title="Personal seed" subtitle="Chapters, life events, goals, habits, people, rules and templates, prepared from your files" action={<Sprout size={18} className="text-ink-3" />}>
      <p className="mb-3 text-[13px] leading-relaxed text-ink-3">
        {seed.data.path}. Applying it again only adds what is missing, so it is safe to press after the file changes.
        {seed.data.allowed ? "" : " It is offered only while this OwnLife has a single account."}
      </p>
      <ErrorNote error={apply.error} />
      <div className="flex justify-end">
        <Button variant="primary" onClick={() => apply.mutate()} loading={apply.isPending} disabled={!seed.data.allowed}>Apply</Button>
      </div>
    </Card>
  );
}

function IntegrationsTab() {
  const qc = useQueryClient();
  const toast = useToast();
  const invalidate = useInvalidateLedger();
  const aw = useQuery({ queryKey: ["aw"], queryFn: () => api.get<{ reachable: boolean; info: { version?: string; hostname?: string } | null; buckets: { id: string }[]; has_web_watcher: boolean; last_synced_at: string | null; last_error: string | null; autosync_minutes: number; url: string }>("/api/integrations/activitywatch") });
  const sync = useMutation({
    mutationFn: () => api.post<{ created: number; from: string; to: string }>("/api/integrations/activitywatch/sync"),
    onSuccess: (r) => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["aw"] });
      toast(`${r.created} blocks imported from ActivityWatch`, "good");
    },
  });
  const a = aw.data;
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Card title="ActivityWatch" subtitle="Automatic, local time tracking: apps, websites, YouTube videos — real minutes" action={<Activity size={18} className="text-ink-3" />}>
        {!a ? (
          <Spinner />
        ) : (
          <div className="space-y-2.5">
            <Status ok={a.reachable}>{a.reachable ? `Running — v${a.info?.version ?? "?"} on ${a.info?.hostname ?? "?"}` : `Not reachable at ${a.url}`}</Status>
            {a.reachable && <Status ok={a.has_web_watcher}>{a.has_web_watcher ? "Browser watcher present (per-video minutes)" : "No browser watcher: install the ActivityWatch Web Watcher extension"}</Status>}
            <p className="text-[13px] text-ink-3">
              Last sync: {a.last_synced_at ? new Date(a.last_synced_at).toLocaleString() : "never"} · automatic every {a.autosync_minutes || "—"} min
            </p>
            {a.last_error && <ErrorNote error={new Error(a.last_error)} />}
            <ErrorNote error={sync.error} />
            <Button variant="primary" icon={<RefreshCw size={15} />} onClick={() => sync.mutate()} loading={sync.isPending} disabled={!a.reachable}>Sync now</Button>
            {!a.reachable && (
              <p className="pt-2 text-[12px] leading-relaxed text-ink-3">
                Install from activitywatch.net, start it (it lives in the system tray), then add the browser extension. OwnLife reads it through its local API; nothing leaves the machine.
              </p>
            )}
          </div>
        )}
      </Card>
      <FilesCard />
      <ExtensionCard />
    </div>
  );
}

function DataTab() {
  const toast = useToast();
  const backups = useQuery({ queryKey: ["backups"], queryFn: () => api.get<{ name: string; bytes: number; created: string }[]>("/api/system/backups") });
  const mirror = useQuery({ queryKey: ["backup-mirror"], queryFn: () => api.get<BackupMirror>("/api/system/backup-mirror") });
  const backup = useMutation({
    mutationFn: () => api.post<{ path: string; mirror: string | null; mirror_error: string | null }>("/api/system/backup"),
    onSuccess: (r) => {
      backups.refetch();
      mirror.refetch();
      toast(r.mirror ? "Backup written, and copied off this disk" : r.mirror_error ? `Backup written; the copy failed: ${r.mirror_error}` : "Backup written", r.mirror_error ? "critical" : "good");
    },
  });
  const m = mirror.data;
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const change = useMutation({
    mutationFn: () => api.post("/api/auth/change-password", pw),
    onSuccess: () => {
      setPw({ current_password: "", new_password: "" });
      toast("Password changed; other devices were logged out", "good");
    },
  });
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <SeedCard />
      <Card title="Backups" subtitle="Automatic once a day; the last 14 are kept in backend/data/backups — and one before every database upgrade" action={<Database size={18} className="text-ink-3" />}>
        <div className="mb-3 flex gap-2">
          <Button variant="primary" onClick={() => backup.mutate()} loading={backup.isPending}>Back up now</Button>
          <a href="/api/system/export" download>
            <Button icon={<Download size={15} />}>Export everything (JSON)</Button>
          </a>
        </div>
        <ul className="divide-y divide-line text-[13px]">
          {backups.data?.map((b) => (
            <li key={b.name} className="flex justify-between py-1.5">
              <span className="text-ink-2">{b.name}</span>
              <span className="num text-ink-3">{(b.bytes / 1e6).toFixed(2)} MB</span>
            </li>
          ))}
        </ul>
        <div className="mt-3 border-t border-line pt-3 text-[13px]">
          {!m ? null : !m.dir ? (
            <Status ok={false}>
              Only on this disk. Add <code>BACKUP_MIRROR_DIR=E:\OwnLifeBackups</code> (a USB stick, a share or a synced folder) to{" "}
              <code>backend\.env</code> and restart: each new backup is then copied there.
            </Status>
          ) : !m.reachable ? (
            <Status ok={false}>Copies go to {m.dir} — not reachable now. Plug it in: the newest backup follows within the hour.</Status>
          ) : (
            <Status ok={!m.behind}>
              Also copied to {m.dir}: {m.copies.length} there{m.copies[0] ? `, newest ${m.copies[0].created.replace("T", " ").slice(0, 16)}` : ""}
              {m.behind ? " — the newest backup follows within the hour" : ""}.
            </Status>
          )}
          <p className="mt-2 text-[12px] text-ink-3">The copy is the database as it is, journal included, not encrypted: a drive you keep yourself is the safest place.</p>
        </div>
      </Card>
      <Card title="Password" action={<KeyRound size={18} className="text-ink-3" />}>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); change.mutate(); }}>
          <Field label="Current password"><Input type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} required /></Field>
          <Field label="New password" hint="At least 10 characters"><Input type="password" autoComplete="new-password" minLength={10} value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} required /></Field>
          <ErrorNote error={change.error} />
          <div className="flex justify-end"><Button type="submit" variant="primary" loading={change.isPending}>Change</Button></div>
        </form>
      </Card>
    </div>
  );
}

export function SettingsPage() {
  const [params] = useSearchParams();
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "profile");
  const { data: profile } = useProfile();
  return (
    <div>
      <PageHeader title="Settings" />
      <Tabs
        className="mb-5 flex-wrap"
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "profile", label: "Profile" },
          { id: "rituals", label: "Rituals" },
          { id: "privacy", label: "Vocabulary & privacy" },
          { id: "ai", label: "AI" },
          { id: "categories", label: "Categories & rules" },
          { id: "integrations", label: "Integrations" },
          { id: "data", label: "Data & security" },
        ]}
      />
      {!profile ? (
        <Spinner />
      ) : (
        <>
          {tab === "profile" && <ProfileTab profile={profile} />}
          {tab === "rituals" && <RitualsTab />}
          {tab === "privacy" && <PrivacyTab profile={profile} />}
          {tab === "ai" && <AiTab profile={profile} />}
          {tab === "categories" && <CategoriesTab />}
          {tab === "integrations" && <IntegrationsTab />}
          {tab === "data" && <DataTab />}
        </>
      )}
    </div>
  );
}

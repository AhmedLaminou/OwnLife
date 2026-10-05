// Shapes returned by the OwnLife API (backend/app/serializers.py and routers).

export type Kind =
  | "core"
  | "growth"
  | "work"
  | "spirit"
  | "social"
  | "body"
  | "maintenance"
  | "noise"
  | "destructive"
  | "uncategorized";

export interface User {
  id: number;
  email: string;
  display_name: string;
  created_at: string;
}

export interface AuthStatus {
  needs_setup: boolean;
  authenticated: boolean;
  user: User | null;
  registration_open: boolean;
}

export interface GlossaryItem {
  term: string;
  meaning: string;
  private: boolean;
}

export interface Profile {
  display_name: string;
  email: string;
  birth_date: string | null;
  life_expectancy_years: number;
  timezone: string;
  currency: string;
  awakening_date: string | null;
  mission_title: string | null;
  mission_text: string | null;
  focus_target_hours: number;
  stretch_focus_hours: number;
  sleep_target_hours: number;
  noise_budget_hours: number;
  wake_target: string | null;
  bed_target: string | null;
  glossary: GlossaryItem[];
  redactions: { pattern: string; replace: string }[];
  ai_settings: { mode?: string; models?: string[] };
}

export interface Category {
  id: number;
  name: string;
  kind: Kind;
  color: string | null;
  icon: string | null;
  parent_id: number | null;
  is_private: boolean;
  archived: boolean;
  sort: number;
}

export interface PersonRef {
  id: number;
  name: string;
}

export interface TimeEntry {
  id: number;
  title: string;
  category_id: number | null;
  category: { id: number; name: string; kind: Kind; icon: string | null } | null;
  kind: Kind;
  started_at: string;
  ended_at: string | null;
  running: boolean;
  duration_seconds: number;
  source: string;
  is_estimate: boolean;
  category_locked: boolean;
  location: string | null;
  notes: string | null;
  goal_id: number | null;
  media_item_id: number | null;
  focus_rating: number | null;
  is_private: boolean;
  people: PersonRef[];
  meta: Record<string, unknown> | null;
}

/** A timer's entry, with the time of its whole session (every segment between pauses). */
export interface TimerEntry extends TimeEntry {
  session_seconds: number;
}

export interface TimerState {
  running: TimerEntry | null;
  paused: TimerEntry[];
}

export type KindSeconds = Partial<Record<Kind, number>>;

export interface PlanBlock {
  id: number;
  date: string;
  start: string;
  end: string;
  start_minute: number;
  end_minute: number;
  title: string;
  category_id: number | null;
  kind: Kind;
  category: string | null;
  is_fixed: boolean;
  notes: string | null;
  matched_seconds?: number;
  planned_seconds?: number;
}

export interface DayEntry extends TimeEntry {
  /** Seconds this entry adds to the day once higher-ranked sources are counted. */
  counted_seconds: number;
  window_seconds: number;
}

export interface Overlap {
  entry_id: number;
  covered_by: number;
  seconds: number;
  start: string;
  end: string;
  /** Which of the two the totals use. */
  counted: "yours" | "automatic";
}

export interface DayView {
  date: string;
  entries: DayEntry[];
  overlaps: Overlap[];
  plan_blocks: PlanBlock[];
  totals_by_kind: KindSeconds;
  totals_by_category: { category_id: number | null; name: string; kind: Kind; seconds: number }[];
  covered_seconds: number;
  untracked_seconds: number;
  targets: { focus_hours: number; stretch_hours: number; noise_budget_hours: number; sleep_hours: number };
}

export interface RangeStats {
  start: string;
  end: string;
  kinds: Kind[];
  days: { date: string; kinds: KindSeconds; total: number }[];
  totals_by_kind: KindSeconds;
  totals_by_category: { category_id: number | null; name: string; kind: Kind; seconds: number }[];
  top_titles: { title: string; seconds: number }[];
  tracked_days: number;
  avg_by_kind_per_tracked_day: KindSeconds;
  targets: { focus_hours: number; noise_budget_hours: number };
}

export interface HabitDay {
  date: string;
  status: string | null;
}

export interface HabitStats {
  habit_id: number;
  kind: "build" | "quit";
  current_streak: number;
  best_streak: number;
  today: string | null;
  calendar: HabitDay[];
  done_last_30?: number;
  rate_last_30?: number | null;
  clean_since?: string;
  relapses_total?: number;
  relapses_last_30?: number;
  urges_resisted_total?: number;
  urges_resisted_last_30?: number;
  next_milestone?: number | null;
  days_to_next_milestone?: number | null;
  relapse_hours?: number[];
  relapse_weekdays?: number[];
}

export interface Habit {
  id: number;
  name: string;
  description: string | null;
  kind: "build" | "quit";
  target_per_week: number;
  rule: { type: string; kind?: Kind; hours?: number } | null;
  start_date: string;
  is_private: boolean;
  color: string | null;
  icon: string | null;
  archived: boolean;
  sort: number;
  stats: HabitStats;
}

export interface DashboardHabit extends Omit<Habit, "stats"> {
  today: string | null;
  current_streak: number;
  best_streak: number;
  next_milestone?: number | null;
  recent: HabitDay[];
}

export interface Milestone {
  id: number;
  title: string;
  level: string;
  status: string;
  target_date: string;
  days_left: number;
}

export interface Dashboard {
  today: string;
  now: string;
  display_name: string;
  awakening_day: number | null;
  mission_title: string | null;
  kinds_today: KindSeconds;
  targets: { focus_hours: number; stretch_hours: number; noise_budget_hours: number; sleep_hours: number };
  week: { date: string; kinds: KindSeconds }[];
  timer: TimeEntry | null;
  habits: DashboardHabit[];
  journal_today: boolean;
  pending_drafts: number;
  milestones: Milestone[];
  life: { days_alive?: number; weeks_alive?: number; total_weeks?: number; age_years?: number };
}

export interface Projection {
  kind: Kind;
  avg_hours_per_day: number;
  hours_until_60: number;
  continuous_years_until_60: number;
  hours_remaining_life: number;
  continuous_years_remaining: number;
  share_of_waking_day: number;
}

export interface Chapter {
  id: number;
  title: string;
  start_date: string;
  end_date: string | null;
  kind: "past" | "plan";
  color: string | null;
  description: string | null;
  approximate: boolean;
  sort: number;
}

export interface LifeOverview {
  configured: boolean;
  today: string;
  birth_date?: string;
  life_expectancy_years?: number;
  end_date?: string;
  age_years?: number;
  age?: number;
  next_birthday?: string;
  days_to_next_birthday?: number;
  days_alive?: number;
  weeks_alive?: number;
  total_weeks?: number;
  weeks_left?: number;
  days_left?: number;
  days_to_60?: number;
  hours_left?: number;
  sleep_hours_per_day?: number;
  sleep_years_left?: number;
  waking_hours_left?: number;
  discretionary_hours_left?: number;
  measured?: { window_days: number; tracked_days: number; avg_hours_by_kind: KindSeconds };
  projections?: Projection[];
  what_if?: {
    noise_avg_hours: number;
    noise_budget_hours: number;
    reclaimed_hours_per_day: number;
    reclaimed_hours_until_60: number;
    reclaimed_continuous_years_until_60: number;
    core_continuous_years_until_60_if_redirected: number;
  } | null;
  awakening?: {
    date: string;
    days_since: number;
    day_number: number;
    weeks_since: number;
    age_at_awakening: number;
    share_of_life_before: number;
    hours_by_kind: Partial<Record<Kind, number>>;
  } | null;
  chapters: Chapter[];
  events: LifeEvent[];
  milestones: Milestone[];
}

export type EventArea =
  | "education"
  | "family"
  | "faith"
  | "health"
  | "work"
  | "move"
  | "travel"
  | "achievement"
  | "turning_point"
  | "other";

export interface LifeEvent {
  id: number;
  date: string;
  precision: "day" | "month" | "year";
  title: string;
  description: string | null;
  area: EventArea;
  importance: 1 | 2 | 3;
  is_private: boolean;
}

export interface WeekCell {
  hours: Partial<Record<Kind, number>>;
  journal_days: number;
}

export interface JournalEntry {
  id: number;
  entry_date: string;
  day_number: number | null;
  title: string | null;
  excerpt: string;
  body?: string;
  tags: string[];
  mood: number | null;
  is_private: boolean;
  source: string;
  sync_state: SyncState;
  file_name: string | null;
  word_count: number;
  created_at: string;
  updated_at: string;
  body_hash?: string;
  conflict_body?: string | null;
  prev_id?: number | null;
  next_id?: number | null;
  sync?: SyncResult | null;
}

export type SyncState = "synced" | "pending" | "conflict" | "missing" | "local";

export interface SyncResult {
  state: SyncState | "deleted";
  message: string | null;
}

export interface SyncItem {
  type: "journal" | "note";
  id: number;
  label: string;
  date: string | null;
  file_removed: boolean;
}

export interface SyncReport {
  created: number;
  updated: number;
  pushed: number;
  unchanged: number;
  restored: number;
  notes_created: number;
  notes_updated: number;
  notes_pushed: number;
  glossary_added: number;
  conflicts: string[];
  missing: string[];
  warnings: string[];
  errors: string[];
  files_written: string[];
}

export interface SyncStatus {
  configured: boolean;
  pattern: string | null;
  yearly: boolean;
  root: string | null;
  writeback: boolean;
  watching: boolean;
  history_dir: string | null;
  next_day_file: string | null;
  files: { kind: "journal" | "notes"; path: string; name: string; days?: number; notes?: number }[];
  revision: number;
  last_sync_at: string | null;
  last_change_at: string | null;
  last_report: SyncReport | null;
  last_error: string | null;
  conflicts: SyncItem[];
  missing: SyncItem[];
  pending: SyncItem[];
  now: string;
}

export interface Note {
  id: number;
  title: string;
  kind: string;
  excerpt: string;
  body?: string;
  tags: string[];
  is_private: boolean;
  source_path: string | null;
  file_name: string | null;
  sync_state: SyncState;
  word_count: number;
  created_at: string;
  updated_at: string;
  body_hash?: string;
  conflict_body?: string | null;
  sync?: SyncResult | null;
}

export interface SearchHit {
  chunk_id: number;
  source_type: "journal" | "note";
  source_id: number;
  title: string;
  date: string | null;
  day_number: number | null;
  text: string;
  score: number;
  via: string[];
  similarity: number | null;
}

export interface GoalNode {
  id: number;
  parent_id: number | null;
  title: string;
  description: string | null;
  level: "vision" | "objective" | "milestone";
  status: "active" | "done" | "paused" | "dropped";
  start_date: string | null;
  target_date: string | null;
  days_left: number | null;
  progress_mode: "manual" | "children" | "hours" | "metric";
  progress_manual: number;
  hours_target: number | null;
  metric_unit: string | null;
  metric_start: number | null;
  metric_target: number | null;
  metric_current: number | null;
  color: string | null;
  sort: number;
  category_ids: number[];
  invested_hours: number;
  invested_hours_tree: number;
  progress: number;
  children: GoalNode[];
}

export interface MediaItem {
  id: number;
  kind: string;
  title: string;
  creator: string | null;
  url: string | null;
  external_id: string | null;
  category_id: number | null;
  status: "want" | "in_progress" | "done" | "dropped" | "none";
  progress_current: number | null;
  progress_total: number | null;
  progress_unit: string | null;
  rating: number | null;
  notes: string | null;
  updated_at: string;
}

export interface YoutubeSummary {
  start: string;
  end: string;
  videos: number;
  channels_count: number;
  estimated_hours: number;
  hours_by_kind: Partial<Record<Kind, number>>;
  hours_of_day: number[];
  hours_by_month: { month: string; hours: number }[];
  channels: {
    channel: string;
    videos: number;
    hours: number;
    category_id: number | null;
    category: Category | null;
    last_watched: string | null;
  }[];
  unclassified_channels: number;
  measured_hours: number;
}

/** What importing a YouTube or Chrome history file did. */
export interface HistoryImport {
  source: "youtube_takeout" | "chrome_history";
  events_in_file: number;
  new_events: number;
  skipped: number;
  ignored: Record<string, number>;
  from: string;
  to: string;
  estimated_blocks: number;
  channel_lookup: boolean;
}

export interface YoutubeStats {
  events: number;
  first: string | null;
  last: string | null;
  channel_lookup: { state: "idle" | "running" | "error"; done: number; total: number; found?: number; error?: string | null };
}

/** A video the extension measured, for sorting (Watching → YouTube). */
export interface SortedVideo {
  video_id: string;
  title: string;
  channel: string | null;
  seconds: number;
  last: string | null;
  url: string;
  /** Its category — or, for a video still to sort, the model's guess. */
  category: Category | null;
  /** The model already looked at it (and could not tell). */
  asked?: boolean;
}

export interface YoutubeSorting {
  prefs: { strict: boolean; sort: boolean };
  ai: boolean;
  model: string;
  status: { state: "idle" | "running" | "error"; at?: string; asked?: number; sorted?: number; guesses?: number; model?: string; error?: string | null };
  unsorted_category: string;
  to_sort: SortedVideo[];
  by_model: SortedVideo[];
}

export interface MeasuredToday {
  today: {
    seconds: number;
    channels: { channel: string; seconds: number }[];
    /** Today's noise from every source, against the budget (the extension's badge). */
    noise: { seconds: number; budget_seconds: number; warn_seconds: number; categories: { name: string; seconds: number }[] };
  };
  last_seen: string | null;
  segments: { title: string; channel: string | null; start: string; end: string; seconds: number; url: string }[];
}

export interface ApiToken {
  id: number;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  token?: string;
}

export interface PrayerPrefs {
  city: string;
  latitude: number;
  longitude: number;
  method: string;
  asr: "standard" | "hanafi";
  offsets: Partial<Record<PrayerName, number>>;
  align_planner: boolean;
}

export interface ReminderPrefs {
  enabled: boolean;
  capture: boolean;
  capture_before_bed_minutes: number;
  bedtime: boolean;
  bedtime_text: string;
  morning: boolean;
  auto_review: boolean;
  /** "Still on it?" when a timer runs this long; 0 = never. */
  timer_nudge_minutes: number;
  /** A notification as the day's noise reaches its budget (Settings → Profile). */
  noise_alert: boolean;
  /** Minutes before the budget is spent for a first warning; 0 = none. */
  noise_warn_minutes: number;
  /** Past the budget, again every N minutes; 0 = only once. */
  noise_repeat_minutes: number;
}

export interface Prefs {
  prayer: PrayerPrefs;
  reminders: ReminderPrefs;
  prayer_methods: { id: string; name: string }[];
  notifications_available: boolean;
}

export type PrayerName = "fajr" | "sunrise" | "dhuhr" | "asr" | "maghrib" | "isha";

export interface PrayerDay {
  date: string;
  city: string;
  method: string;
  method_name: string;
  asr: string;
  times: Record<PrayerName, string | null>;
  minutes: Record<PrayerName, number | null>;
  next?: { name: PrayerName; label: string; time: string; at: string; minutes_left: number } | null;
}

export interface RemindersStatus {
  today: { key: "capture" | "bedtime" | "morning"; at: string; time: string; sent: boolean; past: boolean }[];
  last_error: string | null;
  available: boolean;
}

export interface DayReview {
  date: string;
  facts: ReviewFacts;
  text: string | null;
  model: string | null;
  error: string | null;
  updated_at: string | null;
}

export interface Transaction {
  id: number;
  date: string;
  direction: "in" | "out";
  amount: number;
  currency: string;
  item: string;
  category: string;
  counterparty: string | null;
  person_id: number | null;
  note: string | null;
  source: string;
}

/** The built-in window tracker (Settings → Integrations). */
export interface WindowTracker {
  available: boolean;
  enabled: boolean;
  running: boolean;
  last: { app: string; at: string } | null;
  error: string | null;
  today_seconds: number;
  today_blocks: number;
}

/** The second copy of the backups, off this disk (BACKUP_MIRROR_DIR). */
export interface BackupMirror {
  dir: string | null;
  reachable: boolean;
  copies: { name: string; bytes: number; created: string }[];
  behind: boolean;
}

/** An amount found in the journal, waiting for a yes or a no. */
export interface MoneySuggestion {
  id: number;
  date: string;
  journal_entry_id: number | null;
  direction: "in" | "out";
  amount: number;
  currency: string;
  item: string;
  category: string;
  counterparty: string | null;
  quote: string;
  status: "pending" | "added" | "dismissed";
}

export interface JournalMoney {
  suggestions: MoneySuggestion[];
  prefs: { journal_scan: boolean; auto_add: boolean };
  scan: { state: "idle" | "running" | "error"; error?: string | null; new?: number; found?: number; days?: number; model?: string; finished_at?: string };
  last_scan: string | null;
  last_error: string | null;
  days_not_read: number;
  ai_off: boolean;
}

export interface MoneySummary {
  start: string;
  end: string;
  currency: string;
  total_out: number;
  total_in: number;
  net: number;
  avg_out_per_day: number;
  out_by_category: { category: string; amount: number }[];
  by_day: { date: string; out: number; in: number }[];
}

export interface Person {
  id: number;
  name: string;
  relation: string | null;
  notes: string | null;
  tags: string[];
  is_private: boolean;
  created_at: string;
  hours_together?: number;
  entries?: number | TimeEntry[];
  last_seen?: string | null;
  transactions?: Transaction[];
}

export interface Rule {
  id: number;
  field: "channel" | "title" | "url" | "domain" | "app";
  pattern: string;
  is_regex: boolean;
  category_id: number;
  priority: number;
  note: string | null;
}

export interface PlanTemplate {
  id: number;
  name: string;
  weekdays: number[];
  blocks: { start: string; end: string; title: string; category?: string | null; category_id?: number | null; is_fixed?: boolean }[];
}

export interface PlanDay {
  date: string;
  blocks: PlanBlock[];
  entries: TimeEntry[];
  adherence: number | null;
}

export interface ChatThread {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatAction {
  type: string;
  id: number;
  label: string;
  undone?: boolean;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls: { name: string; args: Record<string, unknown>; id: string }[] | null;
  name: string | null;
  meta: { actions?: ChatAction[]; citations?: SearchHit[]; models?: string[] } | null;
  created_at: string;
}

export interface DraftTimeEntry {
  include: boolean;
  title: string;
  category_id: number | null;
  category: string;
  start: string;
  end: string;
  day_offset: number;
  crosses_midnight?: boolean;
  minutes?: number;
  people: string[];
  location: string | null;
  notes: string | null;
  approximate: boolean;
}

export interface CaptureDraftBody {
  date: string;
  summary: string;
  time_entries: DraftTimeEntry[];
  transactions: { include: boolean; item: string; amount: number; direction: "in" | "out"; category: string; counterparty: string | null }[];
  habit_logs: { include: boolean; habit_id: number; habit: string; status: string; time: string | null; note: string | null }[];
  media: { include: boolean; title: string; kind: string; creator: string | null; status: string }[];
  people: { include: boolean; name: string; relation: string | null }[];
  warnings: string[];
  journal_text?: string | null;
  /** Set on drafts the journal reader proposed: the text is already in the journal. */
  origin?: "journal";
  journal_entry_id?: number;
  day_number?: number | null;
}

/** Reading time blocks from the journal (Assistant → Inbox). */
export interface JournalTime {
  prefs: { scan: boolean };
  scan: { state: "idle" | "running" | "error"; error?: string | null; days?: number; lines?: number; blocks?: number; drafts?: number; finished_at?: string };
  last_scan: string | null;
  last_error: string | null;
  days_not_read: number;
  ai_off: boolean;
}

export interface CaptureDraft {
  id: number;
  date: string;
  input_text: string;
  draft: CaptureDraftBody;
  status: "pending" | "committed" | "discarded";
  model: string | null;
  created_at: string;
}

export interface CommitResult {
  created: Record<string, number>;
  errors: string[];
  journal_sync: SyncResult | null;
}

export interface AiStatus {
  mode: string;
  chain: string[];
  cloud_first: boolean;
  has_openrouter_key: boolean;
  openrouter_models: string[];
  ollama: { reachable: boolean; chat_model: boolean; embed_model: boolean; chat_model_name: string; embed_model_name: string };
  free_tier: string;
}

export interface SearchStatus {
  chunks: number;
  embedded: number;
  embedder: { model: string | null; available: boolean; message: string };
  indexing: { state: string; done: number; total: number; error: string | null };
}

export interface ImportReport {
  created: number;
  updated: number;
  unchanged: number;
  conflicts: string[];
  warnings: string[];
  glossary_added: number;
  notes_changed?: number;
}

export interface QuickResult {
  time_entries: { title: string; start: string; end: string; category: string | null; category_id: number | null; new_people: string[]; location: string | null }[];
  transactions: { direction: "in" | "out"; amount: number; item: string; category: string }[];
  habit_logs: { habit: string; status: string }[];
  errors: string[];
  committed: boolean;
}

export interface ReviewFacts {
  date: string;
  hours_by_kind: Partial<Record<Kind, number>>;
  logged_hours: number;
  core_hours: number;
  core_target: number;
  noise_hours: number;
  noise_budget: number;
  sleep_hours: number;
  habits: { name: string; kind: string; status: string | null; streak: number }[];
  money: { spent: number; received: number; currency: string };
  journal_written: boolean;
}

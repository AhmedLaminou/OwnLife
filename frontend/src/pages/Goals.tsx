import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CalendarClock, Pencil, Plus, Target, Trash2 } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Meter, Modal, PageHeader, Select, Spinner, Textarea } from "../components/ui";
import { api } from "../lib/api";
import { longDate, num, relativeDays } from "../lib/format";
import { useCategories } from "../lib/hooks";
import type { GoalNode } from "../lib/types";

type Form = {
  title: string;
  level: GoalNode["level"];
  parent_id: number | null;
  description: string;
  status: GoalNode["status"];
  target_date: string;
  progress_mode: GoalNode["progress_mode"];
  progress: number;
  hours_target: string;
  metric_unit: string;
  metric_start: string;
  metric_target: string;
  metric_current: string;
  category_ids: number[];
};

const blank = (parent: number | null = null): Form => ({
  title: "",
  level: parent ? "objective" : "vision",
  parent_id: parent,
  description: "",
  status: "active",
  target_date: "",
  progress_mode: parent ? "manual" : "children",
  progress: 0,
  hours_target: "",
  metric_unit: "",
  metric_start: "",
  metric_target: "",
  metric_current: "",
  category_ids: [],
});

function flatten(nodes: GoalNode[], depth = 0): { node: GoalNode; depth: number }[] {
  return nodes.flatMap((n) => [{ node: n, depth }, ...flatten(n.children, depth + 1)]);
}

function GoalModal({ open, onClose, goal, parent, all }: { open: boolean; onClose: () => void; goal: GoalNode | null; parent: number | null; all: GoalNode[] }) {
  const qc = useQueryClient();
  const { data: cats = [] } = useCategories();
  const [f, setF] = useState<Form>(blank(parent));
  useEffect(() => {
    if (!open) return;
    setF(goal
      ? {
          title: goal.title, level: goal.level, parent_id: goal.parent_id, description: goal.description ?? "", status: goal.status,
          target_date: goal.target_date ?? "", progress_mode: goal.progress_mode, progress: goal.progress_manual,
          hours_target: goal.hours_target?.toString() ?? "", metric_unit: goal.metric_unit ?? "",
          metric_start: goal.metric_start?.toString() ?? "", metric_target: goal.metric_target?.toString() ?? "",
          metric_current: goal.metric_current?.toString() ?? "", category_ids: goal.category_ids,
        }
      : blank(parent));
  }, [open, goal, parent]);
  const n = (s: string) => (s.trim() === "" ? null : Number(s));
  const save = useMutation({
    mutationFn: () => {
      const body = {
        ...f,
        description: f.description || null,
        target_date: f.target_date || null,
        hours_target: n(f.hours_target),
        metric_unit: f.metric_unit || null,
        metric_start: n(f.metric_start),
        metric_target: n(f.metric_target),
        metric_current: n(f.metric_current),
      };
      return goal ? api.patch(`/api/goals/${goal.id}`, body) : api.post("/api/goals", body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["goals"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/goals/${goal!.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["goals"] });
      onClose();
    },
  });
  return (
    <Modal open={open} onClose={onClose} title={goal ? "Edit goal" : "New goal"} wide>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="grid gap-3 md:grid-cols-[1fr_160px_200px]">
          <Field label="Goal"><Input autoFocus value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required /></Field>
          <Field label="Level">
            <Select value={f.level} onChange={(e) => setF({ ...f, level: e.target.value as Form["level"] })}>
              <option value="vision">Vision</option>
              <option value="objective">Objective</option>
              <option value="milestone">Milestone</option>
            </Select>
          </Field>
          <Field label="Under">
            <Select value={f.parent_id ?? ""} onChange={(e) => setF({ ...f, parent_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">— top level —</option>
              {flatten(all).filter((x) => x.node.id !== goal?.id).map(({ node, depth }) => (
                <option key={node.id} value={node.id}>{"· ".repeat(depth)}{node.title}</option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="Why / what exactly"><Textarea rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Progress measured by">
            <Select value={f.progress_mode} onChange={(e) => setF({ ...f, progress_mode: e.target.value as Form["progress_mode"] })}>
              <option value="manual">My estimate (%)</option>
              <option value="children">Its sub-goals</option>
              <option value="hours">Hours invested</option>
              <option value="metric">A number (kg, score…)</option>
            </Select>
          </Field>
          <Field label="Target date"><Input type="date" value={f.target_date} onChange={(e) => setF({ ...f, target_date: e.target.value })} /></Field>
          <Field label="Status">
            <Select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as Form["status"] })}>
              <option value="active">Active</option>
              <option value="paused">Paused</option>
              <option value="done">Done</option>
              <option value="dropped">Dropped</option>
            </Select>
          </Field>
        </div>
        {f.progress_mode === "manual" && (
          <Field label={`Progress: ${f.progress}%`}>
            <input type="range" min={0} max={100} value={f.progress} onChange={(e) => setF({ ...f, progress: Number(e.target.value) })} className="w-full accent-[var(--accent)]" />
          </Field>
        )}
        {f.progress_mode === "hours" && (
          <Field label="Hours to invest" hint="Counted from the categories below, from the goal's start">
            <Input type="number" min={1} value={f.hours_target} onChange={(e) => setF({ ...f, hours_target: e.target.value })} />
          </Field>
        )}
        {f.progress_mode === "metric" && (
          <div className="grid grid-cols-4 gap-3">
            <Field label="Unit"><Input value={f.metric_unit} onChange={(e) => setF({ ...f, metric_unit: e.target.value })} placeholder="kg" /></Field>
            <Field label="Start"><Input type="number" value={f.metric_start} onChange={(e) => setF({ ...f, metric_start: e.target.value })} /></Field>
            <Field label="Now"><Input type="number" value={f.metric_current} onChange={(e) => setF({ ...f, metric_current: e.target.value })} /></Field>
            <Field label="Target"><Input type="number" value={f.metric_target} onChange={(e) => setF({ ...f, metric_target: e.target.value })} /></Field>
          </div>
        )}
        <Field label="Time in these categories counts toward it">
          <div className="flex flex-wrap gap-1.5">
            {cats.filter((c) => ["core", "growth", "work", "body", "spirit"].includes(c.kind)).map((c) => {
              const on = f.category_ids.includes(c.id);
              return (
                <button type="button" key={c.id} onClick={() => setF({ ...f, category_ids: on ? f.category_ids.filter((x) => x !== c.id) : [...f.category_ids, c.id] })}>
                  <Badge tone={on ? "accent" : "neutral"}>{c.name}</Badge>
                </button>
              );
            })}
          </div>
        </Field>
        <ErrorNote error={save.error} />
        <div className="flex justify-between">
          {goal ? <Button type="button" variant="ghost" icon={<Trash2 size={15} />} onClick={() => confirm("Delete this goal and its sub-goals?") && remove.mutate()}>Delete</Button> : <span />}
          <Button type="submit" variant="primary" loading={save.isPending}>Save</Button>
        </div>
      </form>
    </Modal>
  );
}

function GoalRow({ g, depth, onEdit, onAdd }: { g: GoalNode; depth: number; onEdit: (g: GoalNode) => void; onAdd: (parent: number) => void }) {
  const muted = g.status === "dropped" || g.status === "paused";
  return (
    <>
      <motion.div
        initial={{ opacity: 0, x: -6 }}
        animate={{ opacity: muted ? 0.55 : 1, x: 0 }}
        className={clsx("group grid grid-cols-[1fr_auto] items-center gap-4 rounded-xl px-3 py-3 hover:bg-panel-hover", depth > 0 && "border-l border-line")}
        style={{ marginLeft: depth * 22 }}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className={clsx("font-medium text-ink", depth === 0 ? "text-[16px]" : "text-[14px]")}>{g.title}</p>
            {g.level === "milestone" && <Badge tone="amber">milestone</Badge>}
            {g.status !== "active" && <Badge>{g.status}</Badge>}
            {g.target_date && (
              <span className="inline-flex items-center gap-1 text-[12px] text-ink-3">
                <CalendarClock size={12} /> {longDate(g.target_date)} · {relativeDays(g.days_left ?? 0)}
              </span>
            )}
          </div>
          {g.description && <p className="mt-0.5 line-clamp-2 text-[12px] text-ink-3">{g.description}</p>}
          <div className="mt-2 grid max-w-xl grid-cols-[1fr_auto] items-center gap-3">
            <Meter value={g.progress} max={100} label={`${g.title} progress`} height={6} color={g.status === "done" ? "var(--good)" : "var(--k-core)"} />
            <span className="num text-[12px] text-ink-2">
              {Math.round(g.progress)}%
              {g.progress_mode === "metric" && g.metric_current !== null && ` · ${g.metric_current}${g.metric_unit ?? ""} → ${g.metric_target}${g.metric_unit ?? ""}`}
              {(g.invested_hours_tree > 0 || g.progress_mode === "hours") && ` · ${num(g.invested_hours_tree, 1)}h${g.hours_target ? ` / ${g.hours_target}h` : ""}`}
            </span>
          </div>
        </div>
        <div className="flex gap-1 opacity-60 group-hover:opacity-100">
          <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => onAdd(g.id)} aria-label="Add sub-goal" />
          <Button size="sm" variant="ghost" icon={<Pencil size={14} />} onClick={() => onEdit(g)} aria-label="Edit" />
        </div>
      </motion.div>
      {g.children.map((c) => (
        <GoalRow key={c.id} g={c} depth={depth + 1} onEdit={onEdit} onAdd={onAdd} />
      ))}
    </>
  );
}

export function GoalsPage() {
  const { data, isLoading } = useQuery({ queryKey: ["goals"], queryFn: () => api.get<GoalNode[]>("/api/goals") });
  const [modal, setModal] = useState<{ open: boolean; goal: GoalNode | null; parent: number | null }>({ open: false, goal: null, parent: null });
  return (
    <div>
      <PageHeader
        title="Goals"
        subtitle="Vision → objectives → milestones. Hours logged in a goal's categories count as invested."
        actions={<Button variant="primary" icon={<Plus size={15} />} onClick={() => setModal({ open: true, goal: null, parent: null })}>Goal</Button>}
      />
      {isLoading ? (
        <Spinner />
      ) : data?.length ? (
        <div className="space-y-5">
          {data.map((root, i) => (
            <Card key={root.id} delay={i * 0.05}>
              <GoalRow g={root} depth={0} onEdit={(g) => setModal({ open: true, goal: g, parent: null })} onAdd={(p) => setModal({ open: true, goal: null, parent: p })} />
            </Card>
          ))}
        </div>
      ) : (
        <Card><Empty icon={<Target size={24} />} title="No goals yet">Start with the vision; the rest hangs under it.</Empty></Card>
      )}
      <GoalModal open={modal.open} goal={modal.goal} parent={modal.parent} all={data ?? []} onClose={() => setModal({ open: false, goal: null, parent: null })} />
    </div>
  );
}

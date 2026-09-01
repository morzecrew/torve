import React from "react";
import {
  DataTable, Badge, Chip, Card, Empty,
  fmt$, fmtK, ago, when, STATE_COLOR, SEV_COLOR,
} from "./lib.jsx";

const col = (header, accessor, opts = {}) => ({
  header,
  accessorFn: typeof accessor === "function" ? accessor : (r) => r[accessor],
  cell: opts.cell ? (c) => opts.cell(c.row.original) : (c) => c.getValue() ?? "—",
  meta: { cls: opts.cls, num: opts.num },
  sortUndefined: "last",
});

const Search = ({ value, onChange, placeholder }) => (
  <input
    type="search"
    value={value}
    placeholder={placeholder}
    onChange={(e) => onChange(e.target.value)}
  />
);

/* ---------- Board ---------- */

export function Board({ ctx }) {
  const [q, setQ] = React.useState("");
  const [states, setStates] = React.useState(new Set());
  const tasks = ctx.tasks;
  const by = {};
  for (const t of tasks) (by[t.state] ??= []).push(t);
  const active = ["running", "claimed", "gated", "reviewed"]
    .reduce((a, s) => a + (by[s]?.length || 0), 0);

  let rows = tasks.slice().reverse();
  if (states.size) rows = rows.filter((t) => states.has(t.state));

  const columns = [
    col("task", "id", { cls: "id" }),
    col("state", "state", { cell: (t) => <Badge color={STATE_COLOR[t.state]}>{t.state}</Badge> }),
    col("role", "role", { cls: "dim" }),
    col("rfc", (t) => (t.rfc || "").replace("rfcs/", "").replace(".md", ""), { cls: "id dim" }),
    col("phase", "phase", { num: true, cls: "dim" }),
    col("attempts", (t) => t.attempts || undefined, { num: true }),
    col("escalation", "escalation", {
      cls: "wrap",
      cell: (t) => (t.escalation ? <Badge color="red">{t.escalation}</Badge> : ""),
    }),
    col("parent", "parent", { cls: "id dim" }),
  ];

  return (
    <>
      <div className="flex gap-3 flex-wrap mb-4">
        <Card k="shipped" v={by.shipped?.length || 0} s="landed in history" accent="green" />
        <Card k="active" v={active} s="in the loop now" accent="blue" />
        <Card k="escalated" v={by.escalated?.length || 0} s="awaiting the operator"
          accent={by.escalated?.length ? "red" : undefined} />
        <Card k="ready" v={by.ready?.length || 0} s="awaiting merge" />
      </div>
      <div className="flex gap-2.5 items-center flex-wrap mb-3.5">
        <Search value={q} onChange={setQ} placeholder="filter id / rfc / role…" />
        {Object.entries(by).sort((a, b) => b[1].length - a[1].length).map(([s, list]) => (
          <Chip key={s} on={states.has(s)} color={STATE_COLOR[s]}
            onClick={() => setStates((p) => { const n = new Set(p); n.has(s) ? n.delete(s) : n.add(s); return n; })}>
            {s} <span className="opacity-70">{list.length}</span>
          </Chip>
        ))}
      </div>
      <DataTable columns={columns} data={rows} globalFilter={q} empty="no tasks match" />
    </>
  );
}

/* ---------- Runs ---------- */

export function Runs({ live }) {
  const runs = live?.runs || [];
  const columns = [
    col("task", (r) => r.task_id || r.task, { cls: "id" }),
    col("state", "state", { cell: (r) => <Badge color={STATE_COLOR[r.state]}>{r.state}</Badge> }),
    col("attempts", "attempts", { num: true }),
    col("heartbeat", (r) => r.heartbeat, { cell: (r) => ago(r.heartbeat), cls: "dim" }),
    col("escalation", (r) => r.escalation?.reason, {
      cls: "wrap",
      cell: (r) =>
        r.escalation ? (
          <>
            <Badge color="red">{r.escalation.reason}</Badge>{" "}
            <span className="text-[var(--dim)]">{(r.escalation.detail || "").slice(0, 200)}</span>
          </>
        ) : "",
    }),
  ];
  return (
    <>
      <p className="text-xs text-[var(--faint)] mb-3">live run states · polled every 3s</p>
      <DataTable columns={columns} data={runs} empty="no live runs — the board is quiet" />
    </>
  );
}

/* ---------- Findings / Proposals (expandable) ---------- */

function ExpandList({ items, renderSummary, renderBody, empty }) {
  if (!items.length) return <Empty>{empty}</Empty>;
  return (
    <div className="glass overflow-hidden">
      {items.map((x, i) => (
        <details key={i} className="frow border-b border-white/5 last:border-0">
          <summary className="flex gap-2.5 items-baseline px-4 py-2.5 hover:bg-white/[.03]">
            {renderSummary(x)}
          </summary>
          <div className="px-4 pb-3 text-[12.5px] text-[var(--dim)]">{renderBody(x)}</div>
        </details>
      ))}
    </div>
  );
}

const Evidence = ({ children }) =>
  children ? (
    <pre className="mono whitespace-pre-wrap break-words bg-black/30 border border-white/10 rounded-lg px-3 py-2 mt-1.5">
      {children}
    </pre>
  ) : null;

export function Findings({ ctx }) {
  const [q, setQ] = React.useState("");
  const [sev, setSev] = React.useState(new Set());
  const [hide, setHide] = React.useState(true);
  let items = ctx.findings.slice().reverse();
  if (hide) items = items.filter((f) => !f.possibly_addressed);
  if (sev.size) items = items.filter((f) => sev.has(f.severity));
  if (q) items = items.filter((f) =>
    (f.claim + " " + f.review + " " + f.target).toLowerCase().includes(q.toLowerCase()));

  const counts = {};
  for (const f of ctx.findings) counts[f.severity] = (counts[f.severity] || 0) + 1;

  return (
    <>
      <div className="flex gap-2.5 items-center flex-wrap mb-3.5">
        <Search value={q} onChange={setQ} placeholder="filter claim / task…" />
        {["blocker", "major", "minor", "nit"].filter((s) => counts[s]).map((s) => (
          <Chip key={s} on={sev.has(s)} color={SEV_COLOR[s]}
            onClick={() => setSev((p) => { const n = new Set(p); n.has(s) ? n.delete(s) : n.add(s); return n; })}>
            {s} <span className="opacity-70">{counts[s]}</span>
          </Chip>
        ))}
        <Chip on={hide} onClick={() => setHide(!hide)}>hide possibly addressed</Chip>
      </div>
      <ExpandList
        items={items}
        empty="ledger clear"
        renderSummary={(f) => (
          <>
            <Badge color={SEV_COLOR[f.severity]}>{f.severity}</Badge>
            <span className="mono text-[var(--faint)] whitespace-nowrap">{f.review}→{f.target}</span>
            <span className="flex-1 truncate">{f.claim}</span>
            {f.possibly_addressed && <Badge color="green">possibly addressed</Badge>}
          </>
        )}
        renderBody={(f) => (
          <>
            <div className="whitespace-pre-wrap">{f.claim}</div>
            <Evidence>{f.evidence}</Evidence>
          </>
        )}
      />
    </>
  );
}

export function Proposals({ ctx }) {
  const [q, setQ] = React.useState("");
  const [hide, setHide] = React.useState(true);
  let items = ctx.proposals.slice().reverse();
  if (hide) items = items.filter((p) => !p.possibly_landed);
  if (q) items = items.filter((p) =>
    (p.decision + " " + p.task + " " + p.claim).toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <div className="flex gap-2.5 items-center flex-wrap mb-3.5">
        <Search value={q} onChange={setQ} placeholder="filter decision / task…" />
        <Chip on={hide} onClick={() => setHide(!hide)}>hide possibly landed</Chip>
      </div>
      <ExpandList
        items={items}
        empty="nothing awaiting the author"
        renderSummary={(p) => (
          <>
            <Badge color="violet">{p.decision}</Badge>
            <Badge color={p.grade === "LOCKED" ? "red" : "grey"}>{p.grade}</Badge>
            <span className="mono text-[var(--faint)]">{p.task}</span>
            <span className="flex-1 truncate">{p.proposal || p.claim}</span>
            {p.possibly_landed && <Badge color="green">possibly landed</Badge>}
          </>
        )}
        renderBody={(p) => (
          <>
            <div className="whitespace-pre-wrap"><b className="text-[var(--text)]">claim</b> — {p.claim}</div>
            {p.proposal && (
              <div className="whitespace-pre-wrap mt-1.5"><b className="text-[var(--text)]">proposal</b> — {p.proposal}</div>
            )}
            <Evidence>{p.evidence}</Evidence>
          </>
        )}
      />
    </>
  );
}

/* ---------- Costs ---------- */

export function Costs({ ctx }) {
  const [q, setQ] = React.useState("");
  const rows = ctx.costs;
  const total = rows.reduce((a, r) => a + (+r.cost_usd || 0), 0);
  const byModel = {};
  for (const r of rows) {
    const k = r.model || r.adapter || "?";
    byModel[k] = (byModel[k] || 0) + (+r.cost_usd || 0);
  }

  const columns = [
    col("at", "at", { cls: "id dim", cell: (r) => when(r.at) }),
    col("task", "task", { cls: "id" }),
    col("kind", "kind", { cls: "dim" }),
    col("regime", (r) => (r.config_hash || "").slice(0, 8), { cls: "id dim" }),
    col("model", (r) => r.model || r.adapter, {}),
    col("cost", "cost_usd", { num: true, cell: (r) => fmt$(r.cost_usd) }),
    col("in", "input_tokens", { num: true, cls: "dim", cell: (r) => fmtK(r.input_tokens) }),
    col("cache", "cache_read_tokens", { num: true, cls: "dim", cell: (r) => fmtK(r.cache_read_tokens) }),
    col("out", "output_tokens", { num: true, cls: "dim", cell: (r) => fmtK(r.output_tokens) }),
    col("harness", "harness", { cls: "id dim" }),
  ];

  return (
    <>
      <div className="flex gap-3 flex-wrap mb-4">
        <Card k="total recorded" v={fmt$(total)} s={rows.length + " records"} accent="green" />
        {Object.entries(byModel).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([m, v]) => (
          <Card key={m} k={m} v={fmt$(v)} s="by model" />
        ))}
      </div>
      <div className="flex gap-2.5 items-center flex-wrap mb-3.5">
        <Search value={q} onChange={setQ} placeholder="filter task / model / regime…" />
        <span className="text-xs text-[var(--faint)]">
          tokens appear on records made after the token-shape landing · click headers to sort
        </span>
      </div>
      <DataTable columns={columns} data={rows} globalFilter={q}
        initialSort={[{ id: "at", desc: true }]} empty="no cost records" />
    </>
  );
}

/* ---------- Gates ---------- */

export function Gates({ ctx }) {
  const rows = Object.entries(ctx.gates).map(([name, g]) => ({ name, ...g }));
  const columns = [
    col("gate", "name", { cls: "id" }),
    col("runs", "runs", { num: true }),
    col("failures", "failures", {
      num: true,
      cell: (g) => (g.failures ? <Badge color="amber">{g.failures}</Badge> : "0"),
    }),
    col("fail rate", (g) => (g.runs ? g.failures / g.runs : 0), {
      num: true, cls: "dim",
      cell: (g) => (g.runs ? ((100 * g.failures) / g.runs).toFixed(1) + "%" : "—"),
    }),
    col("flaky", "flaky", {
      num: true,
      cell: (g) => (g.flaky ? <Badge color="red">{g.flaky}</Badge> : "0"),
    }),
    col("bypassed", "bypassed", { num: true, cls: "dim" }),
    col("mean", "mean_duration_s", { num: true, cls: "dim", cell: (g) => g.mean_duration_s.toFixed(2) + "s" }),
    col("max", "max_duration_s", { num: true, cls: "dim", cell: (g) => g.max_duration_s.toFixed(1) + "s" }),
    col("minutes", (g) => g.total_duration_s / 60, {
      num: true, cls: "dim", cell: (g) => (g.total_duration_s / 60).toFixed(1),
    }),
  ];
  return <DataTable columns={columns} data={rows}
    initialSort={[{ id: "runs", desc: true }]} empty="no gate history" />;
}

/* ---------- Programme ---------- */

const PHASE_COLOR = { shipped: "green", in_flight: "blue", blocked: "red", planned: "grey" };

export function Programme({ ctx }) {
  const rows = ctx.programme;
  const columns = [
    col("rfc", "rfc", { cls: "id" }),
    col("title", "title", { cls: "wrap" }),
    col("status", "status", {
      cell: (r) => <Badge color={r.status === "accepted" ? "green" : "grey"}>{r.status}</Badge>,
    }),
    col("impl", "implementation", {
      cell: (r) => (
        <Badge color={{ complete: "green", partial: "blue" }[r.implementation] || "grey"}>
          {r.implementation || "none"}
        </Badge>
      ),
    }),
    col("phases", (r) => Object.keys(r.progress || {}).length, {
      cell: (r) => {
        const pr = r.progress || {};
        const keys = Object.keys(pr).sort((a, b) => +a - +b);
        if (!keys.length) return <span className="text-[var(--faint)]">—</span>;
        return (
          <span className="flex gap-1.5 flex-wrap">
            {keys.map((k) => (
              <Badge key={k} color={PHASE_COLOR[pr[k]] || "grey"}>P{k}</Badge>
            ))}
          </span>
        );
      },
    }),
    col("flags", (r) => r.disagreement || "", {
      cls: "wrap",
      cell: (r) => (
        <span className="flex gap-1.5 flex-wrap">
          {r.disagreement && <Badge color="amber">⚠ {r.disagreement}</Badge>}
          {r.plannable && <Badge color="cyan">plannable</Badge>}
          {(r.unsatisfied_depends_on || []).length > 0 && (
            <Badge color="grey">waits on {r.unsatisfied_depends_on.join(", ")}</Badge>
          )}
        </span>
      ),
    }),
  ];

  const done = rows.filter((r) => r.implementation === "complete").length;
  return (
    <>
      <div className="flex gap-3 flex-wrap mb-4">
        <Card k="documents" v={rows.length} s="in the corpus" />
        <Card k="complete" v={done} s="implementation judged" accent="green" />
        <Card k="partial" v={rows.filter((r) => r.implementation === "partial").length} s="in flight" accent="blue" />
        <Card k="disagreements" v={rows.filter((r) => r.disagreement).length}
          s="assertion vs derived" accent={rows.some((r) => r.disagreement) ? "amber" : undefined} />
      </div>
      <DataTable columns={columns} data={rows} empty="no documents" />
    </>
  );
}

/* ---------- Attention ---------- */

export function Attention({ ctx }) {
  const oa = ctx.spec_quality?.operator_attention;
  if (!oa) return <Empty>no attention data</Empty>;
  return (
    <>
      <div className="flex gap-3 flex-wrap mb-4">
        <Card k="landed changes" v={oa.landed} s="in the window" accent="green" />
        <Card k="feedback" v={`${oa.feedback.joined} / ${oa.feedback.total}`} s="joined / recorded" />
        <Card k="command events" v={`${oa.command_events.joined} / ${oa.command_events.total}`} s="joined / total" />
        <Card k="escalations triaged" v={`${oa.escalations_triaged.joined} / ${oa.escalations_triaged.total}`} s="joined / total" />
        <Card k="human minutes" v={oa.human_minutes_median ?? "—"} s={`median · n=${oa.human_minutes_n} · floor ${oa.floor}`} />
      </div>
      <p className="text-xs text-[var(--faint)] max-w-3xl">{oa.caveat || ctx.spec_quality.caveat}</p>
    </>
  );
}

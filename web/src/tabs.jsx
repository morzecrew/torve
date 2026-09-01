import React from "react";
import {
  DataTable, MultiSelect, Badge, Chip, Card, Empty,
  fmt$, fmtK, fmtDur, ago, when, STATE_COLOR, SEV_COLOR,
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

const Evidence = ({ children }) =>
  children ? (
    <pre className="mono whitespace-pre-wrap break-words bg-black/30 border border-white/10 rounded-lg px-3 py-2 mt-1.5">
      {children}
    </pre>
  ) : null;

/* ---------- Board ---------- */

const BOARD_HIDDEN = new Set(["shipped", "consumed"]);

export function Board({ ctx }) {
  const [q, setQ] = React.useState("");
  const tasks = ctx.tasks;
  const by = {};
  for (const t of tasks) (by[t.state] ??= []).push(t);
  const allStates = Object.keys(by).sort((a, b) => by[b].length - by[a].length);
  const [states, setStates] = React.useState(
    () => new Set(allStates.filter((s) => !BOARD_HIDDEN.has(s))),
  );
  // states appearing later (a run starting) join the selection automatically
  React.useEffect(() => {
    setStates((p) => {
      const n = new Set(p);
      for (const s of allStates) if (!BOARD_HIDDEN.has(s) && !n.has(s)) n.add(s);
      return n.size === p.size ? p : n;
    });
  }, [allStates.join("|")]);

  const active = ["running", "claimed", "gated", "reviewed"]
    .reduce((a, s) => a + (by[s]?.length || 0), 0);
  const rows = tasks.slice().reverse().filter((t) => states.has(t.state));

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
        <MultiSelect
          label="state"
          options={allStates.map((s) => ({ value: s, count: by[s].length }))}
          selected={states}
          onChange={setStates}
        />
        <span className="text-xs text-[var(--faint)]">shipped and consumed hidden by default</span>
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

/* ---------- Findings ---------- */

export function Findings({ ctx }) {
  const [q, setQ] = React.useState("");
  const [sev, setSev] = React.useState(new Set());
  const [hide, setHide] = React.useState(true);
  let rows = ctx.findings.slice().reverse();
  if (hide) rows = rows.filter((f) => !f.possibly_addressed);
  if (sev.size) rows = rows.filter((f) => sev.has(f.severity));

  const counts = {};
  for (const f of ctx.findings) counts[f.severity] = (counts[f.severity] || 0) + 1;
  const sevRank = { blocker: 0, major: 1, minor: 2, nit: 3 };

  const columns = [
    col("severity", (f) => sevRank[f.severity] ?? 9, {
      cell: (f) => <Badge color={SEV_COLOR[f.severity]}>{f.severity}</Badge>,
    }),
    col("review", "review", { cls: "id dim" }),
    col("target", "target", { cls: "id" }),
    col("claim", "claim", {
      cls: "wrap",
      cell: (f) => <span className="line-clamp-2">{f.claim}</span>,
    }),
    col("status", (f) => (f.possibly_addressed ? 1 : 0), {
      cell: (f) => (f.possibly_addressed ? <Badge color="green">possibly addressed</Badge> : ""),
    }),
  ];

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
      <DataTable
        columns={columns}
        data={rows}
        globalFilter={q}
        empty="ledger clear"
        renderDetail={(f) => (
          <>
            <div className="whitespace-pre-wrap text-[var(--text)]">{f.claim}</div>
            <Evidence>{f.evidence}</Evidence>
          </>
        )}
      />
    </>
  );
}

/* ---------- Proposals ---------- */

export function Proposals({ ctx }) {
  const [q, setQ] = React.useState("");
  const [hide, setHide] = React.useState(true);
  let rows = ctx.proposals.slice().reverse();
  if (hide) rows = rows.filter((p) => !p.possibly_landed);

  const columns = [
    col("decision", "decision", { cell: (p) => <Badge color="violet">{p.decision}</Badge> }),
    col("grade", "grade", {
      cell: (p) => <Badge color={p.grade === "LOCKED" ? "red" : "grey"}>{p.grade}</Badge>,
    }),
    col("task", "task", { cls: "id dim" }),
    col("proposal", (p) => p.proposal || p.claim, {
      cls: "wrap",
      cell: (p) => <span className="line-clamp-2">{p.proposal || p.claim}</span>,
    }),
    col("status", (p) => (p.possibly_landed ? 1 : 0), {
      cell: (p) => (p.possibly_landed ? <Badge color="green">possibly landed</Badge> : ""),
    }),
  ];

  return (
    <>
      <div className="flex gap-2.5 items-center flex-wrap mb-3.5">
        <Search value={q} onChange={setQ} placeholder="filter decision / task…" />
        <Chip on={hide} onClick={() => setHide(!hide)}>hide possibly landed</Chip>
      </div>
      <DataTable
        columns={columns}
        data={rows}
        globalFilter={q}
        empty="nothing awaiting the author"
        renderDetail={(p) => (
          <>
            <div className="whitespace-pre-wrap"><b className="text-[var(--text)]">claim</b> — {p.claim}</div>
            {p.proposal && (
              <div className="whitespace-pre-wrap mt-1.5">
                <b className="text-[var(--text)]">proposal</b> — {p.proposal}
              </div>
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
    col("time", "wall_time_s", { num: true, cls: "dim", cell: (r) => fmtDur(r.wall_time_s) }),
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
const IMPLS = ["none", "partial", "complete"];

export function Programme({ ctx }) {
  const all = ctx.programme;
  const counts = {};
  for (const r of all) {
    const k = r.implementation || "none";
    counts[k] = (counts[k] || 0) + 1;
  }
  // complete-and-clean is noise by default; anything with a disagreement
  // always shows regardless of the dropdown.
  const [impls, setImpls] = React.useState(() => new Set(["none", "partial"]));
  const rows = all.filter((r) => impls.has(r.implementation || "none") || r.disagreement);

  const columns = [
    col("rfc", "rfc", { cls: "id" }),
    col("title", "title", { cls: "wrap" }),
    col("status", "status", {
      cell: (r) => <Badge color={r.status === "accepted" ? "green" : "grey"}>{r.status}</Badge>,
    }),
    col("impl", (r) => r.implementation || "none", {
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

  return (
    <>
      <div className="flex gap-3 flex-wrap mb-4">
        <Card k="documents" v={all.length} s="in the corpus" />
        <Card k="complete" v={counts.complete || 0} s="implementation judged" accent="green" />
        <Card k="partial" v={counts.partial || 0} s="in flight" accent="blue" />
        <Card k="disagreements" v={all.filter((r) => r.disagreement).length}
          s="assertion vs derived" accent={all.some((r) => r.disagreement) ? "amber" : undefined} />
      </div>
      <div className="flex gap-2.5 items-center flex-wrap mb-3.5">
        <MultiSelect
          label="implementation"
          options={IMPLS.map((v) => ({ value: v, count: counts[v] || 0 }))}
          selected={impls}
          onChange={setImpls}
        />
        <span className="text-xs text-[var(--faint)]">
          complete hidden by default · a disagreement always shows
        </span>
      </div>
      <DataTable columns={columns} data={rows} empty="nothing needs attention here" />
    </>
  );
}

/* ---------- Attention ---------- */

export function Attention({ ctx }) {
  const oa = ctx.spec_quality?.operator_attention;
  const docs = ctx.spec_quality?.documents || [];
  if (!oa) return <Empty>no attention data</Empty>;

  const columns = [
    col("document", (d) => (d.rfc || "").replace("rfcs/", "").replace(".md", ""), { cls: "id" }),
    col("minted", "minted", { num: true }),
    col("attempts→green", (d) => d.attempts_to_green_median, {
      num: true, cls: "dim",
      cell: (d) =>
        d.attempts_to_green_median == null
          ? "—"
          : `${d.attempts_to_green_median} (n=${d.attempts_to_green_n})`,
    }),
    col("underspecified", (d) => d.escalations_by_reason?.underspecified || 0, {
      num: true,
      cell: (d) => {
        const n = d.escalations_by_reason?.underspecified || 0;
        return n ? <Badge color="red">{n}</Badge> : "0";
      },
    }),
    col("stale", (d) => d.escalations_by_reason?.stale_inheritance || 0, {
      num: true,
      cell: (d) => {
        const n = d.escalations_by_reason?.stale_inheritance || 0;
        return n ? <Badge color="amber">{n}</Badge> : "0";
      },
    }),
    col("drift", "drift_count", { num: true, cls: "dim" }),
    col("spec-drift findings", (d) => (d.spec_drift_findings || []).length, { num: true, cls: "dim" }),
    col("human min", (d) => d.human_minutes_median, {
      num: true, cls: "dim",
      cell: (d) => (d.human_minutes_median == null ? "—" : `${d.human_minutes_median} (n=${d.human_minutes_n})`),
    }),
    col("rework", (d) => d.rework_rate, {
      num: true, cls: "dim",
      cell: (d) => (d.rework_rate == null ? "—" : `${(100 * d.rework_rate).toFixed(0)}% (n=${d.rework_n})`),
    }),
  ];

  return (
    <>
      <div className="flex gap-3 flex-wrap mb-4">
        <Card k="landed changes" v={oa.landed} s="in the window" accent="green" />
        <Card k="feedback" v={`${oa.feedback.joined} / ${oa.feedback.total}`} s="joined / recorded" />
        <Card k="command events" v={`${oa.command_events.joined} / ${oa.command_events.total}`} s="joined / total" />
        <Card k="escalations triaged" v={`${oa.escalations_triaged.joined} / ${oa.escalations_triaged.total}`} s="joined / total" />
        <Card k="human minutes" v={oa.human_minutes_median ?? "—"} s={`median · n=${oa.human_minutes_n} · floor ${oa.floor}`} />
      </div>
      <DataTable
        columns={columns}
        data={docs.filter((d) => d.minted)}
        initialSort={[{ id: "minted", desc: true }]}
        empty="no per-document signals yet"
      />
      <p className="text-xs text-[var(--faint)] max-w-3xl mt-4">{oa.caveat || ctx.spec_quality.caveat}</p>
    </>
  );
}

import React from "react";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";

/* ---------- formatting ---------- */

export const fmt$ = (v) =>
  v == null ? "—" : "$" + (+v).toFixed(v >= 10 ? 2 : 4);

export const fmtK = (v) =>
  v == null
    ? "—"
    : v >= 1e6
      ? (v / 1e6).toFixed(2) + "M"
      : v >= 1e3
        ? (v / 1e3).toFixed(1) + "k"
        : String(v);

export const ago = (iso) => {
  if (!iso) return "—";
  const s = (Date.now() - Date.parse(iso)) / 1e3;
  if (s < 90) return Math.round(s) + "s ago";
  if (s < 5400) return Math.round(s / 60) + "m ago";
  if (s < 172800) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
};

export const when = (iso) =>
  iso ? iso.replace("T", " ").replace(/:\d\dZ$/, "") : "—";

export const STATE_COLOR = {
  shipped: "green", ready: "green", consumed: "grey", unstarted: "grey",
  running: "blue", claimed: "blue", gated: "amber", reviewed: "violet",
  queued: "grey", escalated: "red",
};
export const SEV_COLOR = { blocker: "red", major: "amber", minor: "blue", nit: "grey" };

/* ---------- atoms ---------- */

export const Badge = ({ color = "grey", children }) => (
  <span className={`b b-${color}`}>{children}</span>
);

export const Chip = ({ on, color = "", onClick, children }) => (
  <span className={`chip ${on ? "on" : ""} ${color}`} onClick={onClick}>
    {children}
  </span>
);

export const Card = ({ k, v, s, accent }) => (
  <div className="glass px-4 py-3 min-w-[9.5rem] flex-1">
    <div className="text-[11px] uppercase tracking-wider text-[var(--dim)]">{k}</div>
    <div
      className="text-[1.45rem] font-semibold tabular-nums mt-0.5"
      style={accent ? { color: `var(--${accent})` } : undefined}
    >
      {v}
    </div>
    {s && <div className="text-xs text-[var(--dim)]">{s}</div>}
  </div>
);

export const Empty = ({ children }) => (
  <div className="glass p-8 text-center text-[var(--faint)]">{children}</div>
);

/* ---------- multi-select dropdown ---------- */

export function MultiSelect({ label, options, selected, onChange }) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => {
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const toggle = (v) => {
    const n = new Set(selected);
    n.has(v) ? n.delete(v) : n.add(v);
    onChange(n);
  };
  return (
    <div className="dd" ref={ref}>
      <button className="dd-btn" onClick={() => setOpen(!open)}>
        {label}
        <span className="text-[var(--faint)] text-xs">{selected.size}/{options.length}</span>
        <span className="text-[var(--faint)]">▾</span>
      </button>
      {open && (
        <div className="dd-pop">
          {options.map(({ value, count }) => (
            <div key={value} className={`dd-item ${selected.has(value) ? "on" : ""}`}
              onClick={() => toggle(value)}>
              <span className="box">{selected.has(value) ? "✓" : ""}</span>
              {value}
              {count != null && <span className="n">{count}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- the data table ---------- */

export function DataTable({ columns, data, globalFilter, initialSort = [], empty, renderDetail }) {
  const [sorting, setSorting] = React.useState(initialSort);
  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    globalFilterFn: (row, _id, value) => {
      const hay = Object.values(row.original)
        .filter((v) => typeof v === "string" || typeof v === "number")
        .join(" ")
        .toLowerCase();
      return hay.includes(String(value).toLowerCase());
    },
  });

  const [expanded, setExpanded] = React.useState(() => new Set());
  const rows = table.getRowModel().rows;
  if (!rows.length) return <Empty>{empty || "nothing here"}</Empty>;

  const nCols = columns.length + (renderDetail ? 1 : 0);
  const flip = (id) =>
    setExpanded((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  return (
    <div className="glass overflow-auto max-h-[72vh]">
      <table className="dt">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {renderDetail && <th style={{ width: 28 }} />}
              {hg.headers.map((h) => {
                const dir = h.column.getIsSorted();
                return (
                  <th key={h.id} onClick={h.column.getToggleSortingHandler()}>
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {dir && <span className="arrow">{dir === "asc" ? "↑" : "↓"}</span>}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.map((r) => (
            <React.Fragment key={r.id}>
              <tr
                className={renderDetail ? "expandable" : ""}
                onClick={renderDetail ? () => flip(r.id) : undefined}
              >
                {renderDetail && (
                  <td><span className={`chev ${expanded.has(r.id) ? "open" : ""}`}>▸</span></td>
                )}
                {r.getVisibleCells().map((c) => (
                  <td key={c.id} className={`${c.column.columnDef.meta?.cls || ""} ${c.column.columnDef.meta?.num ? "num" : ""}`}>
                    {flexRender(c.column.columnDef.cell, c.getContext())}
                  </td>
                ))}
              </tr>
              {renderDetail && expanded.has(r.id) && (
                <tr className="detail">
                  <td colSpan={nCols}>{renderDetail(r.original)}</td>
                </tr>
              )}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

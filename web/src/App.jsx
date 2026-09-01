import React from "react";
import { ago } from "./lib.jsx";
import {
  Board, Runs, Findings, Costs, Gates, Programme, Proposals, Attention,
} from "./tabs.jsx";

const TABS = [
  ["board", "Board"], ["runs", "Runs"], ["findings", "Findings"],
  ["costs", "Costs"], ["gates", "Gates"], ["programme", "Programme"],
  ["proposals", "Proposals"], ["attention", "Attention"],
];

const useHashTab = () => {
  const [tab, setTab] = React.useState(location.hash.slice(1) || "board");
  React.useEffect(() => {
    const on = () => setTab(location.hash.slice(1) || "board");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [tab, (t) => { location.hash = t; setTab(t); }];
};

const usePoll = (url, ms) => {
  const [data, setData] = React.useState(null);
  const [err, setErr] = React.useState(false);
  React.useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const r = await fetch(url);
        if (!live) return;
        setData(await r.json());
        setErr(false);
      } catch {
        if (live) setErr(true);
      }
    };
    tick();
    const id = setInterval(tick, ms);
    return () => { live = false; clearInterval(id); };
  }, [url, ms]);
  return [data, err];
};

export default function App() {
  const [tab, setTab] = useHashTab();
  const [ctx, ctxErr] = usePoll("/api/context", 10000);
  const [live] = usePoll("/api/status", 3000);
  const [, bump] = React.useReducer((x) => x + 1, 0);
  React.useEffect(() => { const id = setInterval(bump, 5000); return () => clearInterval(id); }, []);

  const counts = ctx && {
    board: ctx.tasks.length,
    runs: (live?.runs || []).length,
    findings: ctx.findings.filter((f) => !f.possibly_addressed).length,
    costs: ctx.costs.length,
    gates: Object.keys(ctx.gates).length,
    programme: ctx.programme.length,
    proposals: ctx.proposals.filter((p) => !p.possibly_landed).length,
    attention: ctx.spec_quality?.operator_attention?.landed ?? 0,
  };

  const Body = { board: Board, runs: Runs, findings: Findings, costs: Costs,
    gates: Gates, programme: Programme, proposals: Proposals, attention: Attention }[tab] || Board;

  return (
    <>
      <header className="glass-deep sticky top-0 z-20 px-5">
        <div className="flex items-center gap-4 h-13 max-w-[1280px] mx-auto pt-2">
          <span className="font-semibold tracking-wide text-[15px]">
            torve<b className="text-[var(--green)]">·</b>operator
          </span>
          <span className="ml-auto text-xs text-[var(--dim)] flex items-center gap-2">
            <span
              className="inline-block w-[7px] h-[7px] rounded-full transition-colors"
              style={{ background: ctxErr ? "var(--red)" : "var(--green)" }}
            />
            {ctxErr ? "engine unreachable" : ctx ? "projected " + ago(ctx.at) : "connecting…"}
          </span>
        </div>
        <nav className="flex gap-0.5 overflow-x-auto max-w-[1280px] mx-auto">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex items-center gap-2 px-3.5 py-2 text-[13px] whitespace-nowrap border-b-2 transition-colors ${
                tab === id
                  ? "text-[var(--text)] border-[var(--green)]"
                  : "text-[var(--dim)] border-transparent hover:text-[var(--text)]"
              }`}
            >
              {label}
              {counts && (
                <span className="text-[11px] leading-[17px] px-1.5 rounded-full border border-white/10 bg-white/5 text-[var(--dim)]">
                  {counts[id]}
                </span>
              )}
            </button>
          ))}
        </nav>
      </header>
      <main className="max-w-[1280px] mx-auto px-5 pt-5 pb-16">
        {ctx ? <Body ctx={ctx} live={live} /> : (
          <div className="glass p-10 text-center text-[var(--faint)]">
            waiting for the projection…
          </div>
        )}
      </main>
    </>
  );
}

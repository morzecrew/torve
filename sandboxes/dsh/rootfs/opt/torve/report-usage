#!/usr/bin/env node
// Sum the newest dsh session's usage and print one JSON line in the shape
// parse_metadata reads (total_cost_usd / model keys, last JSON line wins).
// dsh emits no usage on stdout in headless; the session log carries it,
// multi-frame zstd-compressed. Cost is computed only when the operator
// supplies prices (USD per 1M tokens) via DSH_PRICE_IN / DSH_PRICE_OUT /
// DSH_PRICE_CACHE — self-reported regime, absent prices report null.
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

function newestSession(root) {
  let best = null;
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) stack.push(p);
      else if (e.name === "session.jsonl.zstd") {
        const m = fs.statSync(p).mtimeMs;
        if (!best || m > best.m) best = { p, m };
      }
    }
  }
  return best && best.p;
}

const file = newestSession(path.join(process.env.HOME || "/tmp", ".dsh", "sessions"));
if (!file) process.exit(0);

let text;
try {
  text = execFileSync("zstd", ["-dc", file], { maxBuffer: 1 << 28 }).toString();
} catch {
  process.exit(0);
}

const totals = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, reasoningTokens: 0 };
let model = null;
let requests = 0;
for (const line of text.split("\n")) {
  if (!line.trim()) continue;
  let o;
  try { o = JSON.parse(line); } catch { continue; }
  const chunk = o && o.data && o.data.chunk;
  if (chunk && chunk.type === "usage" && chunk.usage) {
    requests += 1;
    for (const k of Object.keys(totals)) totals[k] += chunk.usage[k] || 0;
  }
  const src = o && o.data && o.data.message && o.data.message.source;
  if (src && src.model) model = src.model;
}

const priceIn = parseFloat(process.env.DSH_PRICE_IN || "");
const priceOut = parseFloat(process.env.DSH_PRICE_OUT || "");
const priceCache = parseFloat(process.env.DSH_PRICE_CACHE || "");
let cost = null;
if (!Number.isNaN(priceIn) && !Number.isNaN(priceOut)) {
  cost = (totals.inputTokens * priceIn + totals.outputTokens * priceOut) / 1e6;
  if (!Number.isNaN(priceCache)) cost += (totals.cacheReadTokens * priceCache) / 1e6;
  cost = Math.round(cost * 1e6) / 1e6;
}

console.log(JSON.stringify({ total_cost_usd: cost, model, usage: totals, requests }));

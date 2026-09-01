import { Cpu, Layers, Wallet } from "lucide-react"

import type { CostRow } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { formatStamp, formatUsd } from "@/lib/format"

interface CostsProps {
  costs: CostRow[] | undefined
}

function shortHash(hash: string | null): string {
  if (!hash) {
    return "—"
  }

  return hash.length > 10 ? `${hash.slice(0, 10)}…` : hash
}

export function Costs({ costs }: CostsProps) {
  const rows = costs ?? []
  const total = rows.reduce((sum, row) => sum + (row.cost_usd ?? 0), 0)
  const attempts = rows.filter((row) => row.kind === "attempt")
  const shadows = rows.filter((row) => row.kind === "shadow")

  // The token shape, as the projection carries it: spend grouped by the model
  // that did the work (identity falls back to harness, then adapter). Grouping
  // existing facts is rendering, not a new derivation.
  const byModel = new Map<string, { cost: number; count: number }>()

  for (const row of attempts) {
    const model = row.model ?? row.harness ?? row.adapter ?? "unknown"
    const entry = byModel.get(model) ?? { cost: 0, count: 0 }
    entry.cost += row.cost_usd ?? 0
    entry.count += 1
    byModel.set(model, entry)
  }

  const modelShape = [...byModel.entries()].sort((a, b) => b[1].cost - a[1].cost)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Wallet className="size-4 text-primary" aria-hidden />
          Cost and token shape
        </CardTitle>
        <CardDescription>
          spend by attempt against config_hash — every real attempt appears, costless when the
          harness reported nothing
        </CardDescription>
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg border border-border/50 bg-background/30 p-3">
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground uppercase tracking-wider">
              <Wallet className="size-3" aria-hidden /> total
            </div>
            <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
              {formatUsd(total)}
            </div>
          </div>

          <div className="rounded-lg border border-border/50 bg-background/30 p-3">
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground uppercase tracking-wider">
              <Layers className="size-3" aria-hidden /> attempts
            </div>
            <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
              {attempts.length}
            </div>
          </div>

          <div className="rounded-lg border border-border/50 bg-background/30 p-3">
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground uppercase tracking-wider">
              <Cpu className="size-3" aria-hidden /> models
            </div>
            <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
              {byModel.size}
            </div>
          </div>

          <div className="rounded-lg border border-border/50 bg-background/30 p-3">
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground uppercase tracking-wider">
              <Layers className="size-3" aria-hidden /> shadows
            </div>
            <div className="mt-1 font-mono text-lg font-semibold tabular-nums">{shadows.length}</div>
          </div>
        </div>

        {modelShape.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-muted-foreground text-[11px] uppercase tracking-wider">
              by model
            </span>
            {modelShape.map(([model, shape]) => (
              <Badge key={model} variant="outline" className="tabular-nums">
                {model} · {formatUsd(shape.cost)} · {shape.count}
              </Badge>
            ))}
          </div>
        )}

        {rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">no cost records yet</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>at</TableHead>
                <TableHead>task</TableHead>
                <TableHead>kind</TableHead>
                <TableHead>cost</TableHead>
                <TableHead>model</TableHead>
                <TableHead>provider</TableHead>
                <TableHead>harness</TableHead>
                <TableHead>config</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, index) => (
                <TableRow key={index}>
                  <TableCell className="text-muted-foreground">{formatStamp(row.at)}</TableCell>
                  <TableCell className="font-mono font-medium">{row.task ?? "—"}</TableCell>
                  <TableCell>
                    <Badge variant={row.kind === "shadow" ? "secondary" : "default"}>
                      {row.kind}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">{formatUsd(row.cost_usd)}</TableCell>
                  <TableCell className="font-mono">{row.model ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{row.provider ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{row.harness ?? row.adapter ?? "—"}</TableCell>
                  <TableCell className="font-mono text-muted-foreground">
                    {shortHash(row.config_hash)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

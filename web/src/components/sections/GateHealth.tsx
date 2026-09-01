import { ShieldCheck } from "lucide-react"

import type { GateStats } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { formatDuration } from "@/lib/format"

interface GateHealthProps {
  gates: Record<string, GateStats> | undefined
}

export function GateHealth({ gates }: GateHealthProps) {
  const entries = Object.entries(gates ?? {}).sort(([a], [b]) => a.localeCompare(b))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-primary" aria-hidden />
          Gate health
        </CardTitle>
        <CardDescription>
          per-gate outcomes and duration from the telemetry stream — a gate with zero runs has
          never been observed, which is not the same as clean
        </CardDescription>
      </CardHeader>

      <CardContent>
        {entries.length === 0 ? (
          <p className="text-muted-foreground text-sm">no gate runs recorded yet</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {entries.map(([name, gate]) => {
              const clean = Math.max(0, gate.runs - gate.failures - gate.flaky - gate.bypassed)
              const passRate = gate.runs > 0 ? (clean / gate.runs) * 100 : 0

              return (
                <div key={name} className="rounded-lg border border-border/50 bg-background/30 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-sm font-medium">{name}</span>
                    <Badge variant="outline">
                      {gate.runs} {gate.runs === 1 ? "run" : "runs"}
                    </Badge>
                  </div>

                  <div className="mt-3">
                    <Progress value={passRate} aria-label={`${name} pass rate`} />
                  </div>

                  <div className="text-muted-foreground mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] tabular-nums">
                    <span className="text-emerald-300">{clean} pass</span>
                    <span className="text-red-300">{gate.failures} fail</span>
                    {gate.flaky > 0 && <span className="text-amber-300">{gate.flaky} flaky</span>}
                    {gate.bypassed > 0 && (
                      <span className="text-violet-300">{gate.bypassed} bypassed</span>
                    )}
                  </div>

                  <div className="text-muted-foreground mt-1 font-mono text-[11px] tabular-nums">
                    mean {formatDuration(gate.mean_duration_s)} · max{" "}
                    {formatDuration(gate.max_duration_s)}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

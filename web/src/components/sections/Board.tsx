import { Layers, RadioTower } from "lucide-react"

import type { StatusRun, Task } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ageSeconds, formatAge } from "@/lib/format"
import { ACTIVE_RUN_STATES, STATE_ORDER, rfcLabel, stateTone } from "@/lib/presentation"
import { useNow } from "@/hooks/use-now"

interface BoardProps {
  tasks: Task[] | undefined
  decompositions: Record<string, string[]> | undefined
  runs: StatusRun[] | undefined
}

export function Board({ tasks, decompositions, runs }: BoardProps) {
  const now = useNow()

  const byState = new Map<string, Task[]>()

  for (const task of tasks ?? []) {
    const bucket = byState.get(task.state) ?? []
    bucket.push(task)
    byState.set(task.state, bucket)
  }

  const activeRuns = (runs ?? []).filter((run) => ACTIVE_RUN_STATES.has(run.state))
  const totalTasks = tasks?.length ?? 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Layers className="size-4 text-primary" aria-hidden />
          Board
        </CardTitle>
        <CardDescription>
          {totalTasks} {totalTasks === 1 ? "task" : "tasks"} by state, in the order the CLI
          renders them — an empty column is a fact, not a gap
        </CardDescription>
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        {activeRuns.length > 0 && (
          <div className="rounded-lg border border-emerald-400/20 bg-emerald-400/5 px-3 py-2.5">
            <div className="flex items-center gap-2 text-xs font-medium text-emerald-300">
              <RadioTower className="size-3.5" aria-hidden />
              in flight — {activeRuns.length} live {activeRuns.length === 1 ? "run" : "runs"} from
              the status projection
            </div>
            <div className="mt-2 grid gap-1.5 sm:grid-cols-2 xl:grid-cols-4">
              {activeRuns.map((run) => (
                <div
                  key={run.task_id}
                  className="flex items-center justify-between gap-2 rounded-md border border-emerald-400/15 bg-background/40 px-2.5 py-1.5 font-mono text-[11px] tabular-nums"
                >
                  <span className="font-semibold">{run.task_id}</span>
                  <span className="text-muted-foreground">
                    {run.state} · {formatAge(ageSeconds(run.heartbeat, now))} hb
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {STATE_ORDER.map((state) => {
            const bucket = byState.get(state) ?? []

            return (
              <div
                key={state}
                className="rounded-lg border border-border/50 bg-background/30 p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{state}</span>
                  <Badge variant="outline" className={stateTone(state)}>
                    {bucket.length}
                  </Badge>
                </div>

                <div className="mt-2 flex flex-col gap-1.5">
                  {bucket.length === 0 ? (
                    <p className="text-muted-foreground/60 py-1 text-[11px]">—</p>
                  ) : (
                    bucket.map((task) => (
                      <div
                        key={task.id}
                        className="rounded-md border border-border/40 bg-background/40 px-2.5 py-2"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs font-semibold">
                            {task.parent ? `↳ ${task.id}` : task.id}
                          </span>
                          <span
                            className="text-muted-foreground text-[10px] tabular-nums"
                            title={`attempts: ${task.attempts}`}
                          >
                            a{task.attempts}
                          </span>
                        </div>

                        <div className="mt-1 flex flex-wrap items-center gap-1">
                          {task.rfc && (
                            <Badge variant="outline" className="text-[10px]">
                              {rfcLabel(task.rfc)}
                            </Badge>
                          )}
                          {task.role !== "implement" && (
                            <Badge variant="outline" className="text-[10px]">
                              {task.role}
                            </Badge>
                          )}
                          {task.escalation && (
                            <Badge className={stateTone("escalated")} title={task.escalation}>
                              {task.escalation}
                            </Badge>
                          )}
                        </div>

                        {task.phase > 0 && (
                          <div className="text-muted-foreground mt-1 text-[10px] tabular-nums">
                            phase {task.phase}
                          </div>
                        )}

                        {decompositions?.[task.id] && (
                          <div className="text-muted-foreground mt-1 font-mono text-[10px]">
                            children: {decompositions[task.id].join(", ")}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

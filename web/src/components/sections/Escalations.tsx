import { TriangleAlert } from "lucide-react"

import type { EscalationItem } from "@/lib/api"
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
import { formatAge, formatStamp } from "@/lib/format"
import { routeTone } from "@/lib/presentation"

interface EscalationsProps {
  escalations: Record<string, EscalationItem[]> | undefined
}

export function Escalations({ escalations }: EscalationsProps) {
  const byReason = escalations ?? {}
  const reasons = Object.keys(byReason).sort()
  const total = reasons.reduce((sum, reason) => sum + (byReason[reason]?.length ?? 0), 0)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TriangleAlert className="size-4 text-destructive" aria-hidden />
          Escalations
        </CardTitle>
        <CardDescription>
          {total} escalated {total === 1 ? "task" : "tasks"} by reason — the queue&apos;s age is
          the primary signal
        </CardDescription>
      </CardHeader>

      <CardContent>
        {reasons.length === 0 ? (
          <p className="text-muted-foreground text-sm">no escalations — the queue is empty</p>
        ) : (
          <div className="grid gap-3 xl:grid-cols-2">
            {reasons.map((reason) => {
              const items = byReason[reason] ?? []
              const oldest = items.reduce((max, item) => Math.max(max, item.age_s ?? 0), 0)

              return (
                <div key={reason} className="rounded-lg border border-border/50 bg-background/30">
                  <div className="flex items-center justify-between gap-2 border-b border-border/40 px-3 py-2">
                    <span className="text-sm font-medium">{reason}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-muted-foreground text-[11px] tabular-nums">
                        oldest {formatAge(oldest)}
                      </span>
                      <Badge variant="destructive">{items.length}</Badge>
                    </div>
                  </div>

                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>task</TableHead>
                        <TableHead>at</TableHead>
                        <TableHead>age</TableHead>
                        <TableHead>route</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {items.map((item) => (
                        <TableRow key={item.task}>
                          <TableCell className="font-mono font-medium">{item.task}</TableCell>
                          <TableCell className="text-muted-foreground">
                            {formatStamp(item.at)}
                          </TableCell>
                          <TableCell className="tabular-nums">{formatAge(item.age_s)}</TableCell>
                          <TableCell>
                            <Badge variant="outline" className={routeTone(item.route)}>
                              {item.route}
                            </Badge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

import { Activity, CircleAlert, RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import type { ContextReport } from "@/lib/api"
import { ageSeconds, formatAge, formatStamp } from "@/lib/format"
import { useNow } from "@/hooks/use-now"

interface HeaderProps {
  report: ContextReport | null
  error: string | null
  loading: boolean
  onRefresh: () => void
}

export function Header({ report, error, loading, onRefresh }: HeaderProps) {
  const now = useNow()
  const stale = error !== null
  const projectedAt = report?.at ?? null

  return (
    <header className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <div className="grid size-9 place-items-center rounded-lg border border-border/60 bg-card/60 font-mono text-base font-bold text-primary shadow-sm backdrop-blur-xl">
          t
        </div>
        <div>
          <h1 className="text-lg leading-tight font-semibold tracking-tight">
            torve <span className="text-muted-foreground">· serve</span>
          </h1>
          <p className="text-muted-foreground text-xs">read-only projection surface · loopback only</p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <div
          className="flex items-center gap-2.5 rounded-lg border border-border/60 bg-card/60 px-3 py-2 shadow-sm backdrop-blur-xl"
          title={
            stale
              ? "the last poll failed — showing the previous projection"
              : "polling both endpoints every few seconds"
          }
        >
          {stale ? (
            <CircleAlert className="size-4 text-destructive" aria-hidden />
          ) : (
            <Activity
              className={loading ? "size-4 text-muted-foreground" : "size-4 text-emerald-400"}
              aria-hidden
            />
          )}
          <div className="text-right">
            <div className="text-muted-foreground text-[10px] tracking-widest uppercase">
              projected at
            </div>
            <div className="font-mono text-sm tabular-nums">
              {projectedAt ? (
                <>
                  {formatStamp(projectedAt)}{" "}
                  <span className={stale ? "text-destructive" : "text-muted-foreground"}>
                    {formatAge(ageSeconds(projectedAt, now))} ago
                  </span>
                </>
              ) : (
                <span className="text-muted-foreground">connecting…</span>
              )}
            </div>
          </div>
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={onRefresh}
          disabled={loading}
          title="Poll both endpoints now"
        >
          <RefreshCw className={loading ? "animate-spin" : ""} aria-hidden />
          refresh
        </Button>
      </div>
    </header>
  )
}

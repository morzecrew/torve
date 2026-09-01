import { CircleAlert } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Board } from "@/components/sections/Board"
import { Costs } from "@/components/sections/Costs"
import { Escalations } from "@/components/sections/Escalations"
import { Findings } from "@/components/sections/Findings"
import { GateHealth } from "@/components/sections/GateHealth"
import { Header } from "@/components/sections/Header"
import { Programme } from "@/components/sections/Programme"
import { Proposals } from "@/components/sections/Proposals"
import { usePoll } from "@/hooks/use-poll"
import { fetchContext, fetchStatus } from "@/lib/api"

// The polling contract (D-32.6): a few seconds, no push channel. Fast enough
// to feel live, slow enough that an open tab costs nothing.
const POLL_INTERVAL_MS = 5000

export default function App() {
  const context = usePoll(fetchContext, POLL_INTERVAL_MS)
  const status = usePoll(fetchStatus, POLL_INTERVAL_MS)

  const refresh = () => {
    void context.refresh()
    void status.refresh()
  }

  const report = context.data
  const runs = status.data?.runs

  return (
    <div className="min-h-screen">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <Header
          report={report}
          error={context.error}
          loading={context.loading}
          onRefresh={refresh}
        />

        {context.error && !report && (
          <Alert variant="destructive" className="mt-4">
            <CircleAlert aria-hidden />
            <AlertTitle>cannot reach /api/context</AlertTitle>
            <AlertDescription>{context.error}</AlertDescription>
          </Alert>
        )}

        {(context.error || status.error) && (
          <div className="mt-3 flex flex-col gap-1">
            {context.error && report && (
              <p className="text-destructive text-xs">
                context poll failed — showing the last projection: {context.error}
              </p>
            )}
            {status.error && (
              <p className="text-destructive text-xs">status poll failed: {status.error}</p>
            )}
          </div>
        )}

        <main className="mt-6 flex flex-col gap-6">
          <Board tasks={report?.tasks} decompositions={report?.decompositions} runs={runs} />
          <Escalations escalations={report?.escalations} />
          <Findings findings={report?.findings} />
          <Proposals proposals={report?.proposals} />
          <GateHealth gates={report?.gates} />
          <Costs costs={report?.costs} />
          <Programme programme={report?.programme} />
        </main>

        <footer className="text-muted-foreground mt-10 flex flex-wrap items-center justify-between gap-2 border-t border-border/40 pt-4 text-[11px]">
          <span>torve serve — read-only projection surface · loopback only</span>
          <span className="font-mono">schema {report?.schema_version ?? "—"}</span>
        </footer>
      </div>
    </div>
  )
}

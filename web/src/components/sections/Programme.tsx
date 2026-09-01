import { FileText, TriangleAlert } from "lucide-react"

import type { ProgrammeEntry } from "@/lib/api"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { progressTone } from "@/lib/presentation"

interface ProgrammeProps {
  programme: ProgrammeEntry[] | undefined
}

export function Programme({ programme }: ProgrammeProps) {
  const entries = programme ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileText className="size-4 text-primary" aria-hidden />
          Programme
        </CardTitle>
        <CardDescription>
          the RFC graph — status, implementation assertion, and per-phase progress; where
          assertion and derivation disagree, the disagreement is the informative part
        </CardDescription>
      </CardHeader>

      <CardContent>
        {entries.length === 0 ? (
          <p className="text-muted-foreground text-sm">no documents in the corpus</p>
        ) : (
          <div className="flex flex-col gap-3">
            {entries.map((doc) => (
              <div key={doc.rfc} className="rounded-lg border border-border/50 bg-background/30 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm font-semibold">{doc.rfc}</span>
                  <span className="text-sm">{doc.title}</span>
                  <Badge variant="secondary">{doc.status}</Badge>
                  {doc.kind !== "design" && <Badge variant="outline">{doc.kind}</Badge>}
                  {doc.plannable && (
                    <Badge className="border-emerald-400/30 bg-emerald-400/10 text-emerald-300">
                      plannable
                    </Badge>
                  )}
                </div>

                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <span className="text-muted-foreground text-[11px] uppercase tracking-wider">
                    impl
                  </span>
                  <Badge variant="outline">{doc.implementation}</Badge>

                  {Object.entries(doc.progress).map(([phase, progress]) => (
                    <Badge key={phase} variant="outline" className={progressTone(progress)}>
                      P{phase} · {progress}
                    </Badge>
                  ))}

                  {doc.unsatisfied_depends_on.length > 0 && (
                    <span className="text-amber-300 text-[11px]">
                      waits on {doc.unsatisfied_depends_on.join(", ")}
                    </span>
                  )}
                </div>

                {doc.disagreement && (
                  <Alert variant="destructive" className="mt-2 py-2.5">
                    <TriangleAlert aria-hidden />
                    <AlertTitle>assertion disagrees with progress</AlertTitle>
                    <AlertDescription>{doc.disagreement}</AlertDescription>
                  </Alert>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

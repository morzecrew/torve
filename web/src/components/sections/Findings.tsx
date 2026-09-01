import { BookOpenCheck } from "lucide-react"

import type { Finding } from "@/lib/api"
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
import { severityTone } from "@/lib/presentation"

interface FindingsProps {
  findings: Finding[] | undefined
}

export function Findings({ findings }: FindingsProps) {
  const rows = findings ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpenCheck className="size-4 text-primary" aria-hidden />
          Findings ledger
        </CardTitle>
        <CardDescription>
          {rows.length} kept non-blocking {rows.length === 1 ? "finding" : "findings"} from
          landed reviews
        </CardDescription>
      </CardHeader>

      <CardContent>
        {rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">no findings in the ledger</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>target</TableHead>
                <TableHead>severity</TableHead>
                <TableHead>claim</TableHead>
                <TableHead>evidence</TableHead>
                <TableHead>review</TableHead>
                <TableHead>state</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((finding, index) => (
                <TableRow key={`${finding.review}-${index}`}>
                  <TableCell className="font-mono font-medium">{finding.target}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={severityTone(finding.severity)}>
                      {finding.severity}
                    </Badge>
                  </TableCell>
                  <TableCell className="min-w-0 max-w-md text-left align-top whitespace-normal">
                    {finding.claim}
                  </TableCell>
                  <TableCell className="text-muted-foreground min-w-0 max-w-sm text-left align-top whitespace-normal">
                    {finding.evidence}
                  </TableCell>
                  <TableCell className="font-mono text-muted-foreground">
                    {finding.review}
                  </TableCell>
                  <TableCell>
                    {finding.possibly_addressed ? (
                      <Badge variant="secondary">possibly addressed</Badge>
                    ) : (
                      <Badge variant="outline">open</Badge>
                    )}
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

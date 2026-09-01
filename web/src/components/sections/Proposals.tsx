import { Lightbulb } from "lucide-react"

import type { Proposal } from "@/lib/api"
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
import { gradeTone } from "@/lib/presentation"

interface ProposalsProps {
  proposals: Proposal[] | undefined
}

export function Proposals({ proposals }: ProposalsProps) {
  const rows = proposals ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Lightbulb className="size-4 text-primary" aria-hidden />
          Proposals
        </CardTitle>
        <CardDescription>
          {rows.length} divergence {rows.length === 1 ? "entry" : "entries"} carrying a proposal
          — data ready to become decision-table rows
        </CardDescription>
      </CardHeader>

      <CardContent>
        {rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">no open proposals</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>task</TableHead>
                <TableHead>decision</TableHead>
                <TableHead>grade</TableHead>
                <TableHead>claim</TableHead>
                <TableHead>proposal</TableHead>
                <TableHead>evidence</TableHead>
                <TableHead>landed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((proposal, index) => (
                <TableRow key={`${proposal.task}-${index}`}>
                  <TableCell className="font-mono font-medium">{proposal.task}</TableCell>
                  <TableCell className="font-mono">{proposal.decision ?? "—"}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={gradeTone(proposal.grade)}>
                      {proposal.grade ?? "—"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground min-w-0 max-w-sm text-left align-top whitespace-normal">
                    {proposal.claim ?? "—"}
                  </TableCell>
                  <TableCell className="min-w-0 max-w-md text-left align-top whitespace-normal">
                    {proposal.proposal}
                  </TableCell>
                  <TableCell className="font-mono text-muted-foreground max-w-xs truncate">
                    {proposal.evidence ?? "—"}
                  </TableCell>
                  <TableCell>
                    {proposal.possibly_landed ? (
                      <Badge variant="secondary">possibly landed</Badge>
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

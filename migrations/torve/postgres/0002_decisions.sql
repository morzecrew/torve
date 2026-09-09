-- Torve: the decision graph's read (S-0047/the-read-that-does-not-fold-an-execution-log, S-0047/D-7). Owner: torve —
-- this file changes for torve's own reasons, never for a forze upgrade
-- (S-0012/D-1). Forward-only (S-0012/D-4).
--
-- Sources and decisions grow with the corpus; attempts, gates and burn grow
-- with execution. `torve_event_partition` serves the board, which wants a
-- partition's whole tail; this one serves the decision graph, which wants
-- the corpus-sized slice of it and would otherwise fold an execution log to
-- find a few hundred rows.
--
-- Ordered by (created_at, id) for the same reason 0001 is: the timestamp is
-- not a total order under concurrency and the id is the stable tiebreak, so
-- the index carries both or the sort spills.

CREATE INDEX IF NOT EXISTS torve_event_subject_type
    ON public.torve_event (partition, subject_type, created_at, id);

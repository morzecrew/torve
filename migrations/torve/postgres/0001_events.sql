-- Torve: the event log (RFC 0044 §5.1). Owner: torve — this file changes for
-- torve's own reasons, never for a forze upgrade (D-12.1). Forward-only
-- (D-12.4).
--
-- The relation backs the `torve-events` document spec, so every column name
-- is a field name on the aggregate's models and the two must move together;
-- `id`, `rev`, `created_at` and `last_update_at` come from the document base
-- rather than from anything torve declared.
--
-- Append-only is a property of the spec above this table — it declares no
-- update command, so the adapter exposes no update port — and `rev` is here
-- because the document base carries it, not because a row is ever revised.
-- A correction is another event.

CREATE TABLE IF NOT EXISTS public.torve_event (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    rev              integer     NOT NULL DEFAULT 1,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_update_at   timestamptz NOT NULL DEFAULT now(),
    schema_version   integer     NOT NULL DEFAULT 1,
    kind             text        NOT NULL,
    partition        text        NOT NULL,
    subject_type     text        NOT NULL,
    subject_id       text        NOT NULL,
    actor_kind       text        NOT NULL,
    actor_id         text        NOT NULL,
    payload          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    correlation_id   text,
    causation_id     text
);

-- The log's two reads (RFC 0044 §5.7): one subject's history, and one
-- partition's tail. Both order by (created_at, id) — the timestamp is not a
-- total order under concurrency and the id is the stable tiebreak, so the
-- index carries both or the sort spills.
CREATE INDEX IF NOT EXISTS torve_event_subject
    ON public.torve_event (subject_id, created_at, id);

CREATE INDEX IF NOT EXISTS torve_event_partition
    ON public.torve_event (partition, created_at, id);

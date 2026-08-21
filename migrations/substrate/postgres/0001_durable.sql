-- Substrate: durable run store + step journal (forze, schema per the
-- adapter docstrings of PostgresDurableRunStore / the step adapter).
-- Owner: substrate — this file changes when forze is upgraded, never for
-- torve's own reasons (D-12.1). Forward-only (D-12.4).
--
-- Do not trim columns: every read projects cancel_requested_at /
-- cancel_refused_at, and a missing column makes lease renewal raise, which a
-- heartbeat reads as lease lost.

CREATE TABLE IF NOT EXISTS public.torve_durable_run (
    run_id text NOT NULL,
    name text NOT NULL,
    status text NOT NULL,
    idempotency_key text,
    input jsonb,
    output jsonb,
    error text,
    tenant_id uuid,
    attempts integer NOT NULL DEFAULT 0,
    leased_until timestamptz,
    available_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    cancel_requested_at timestamptz,
    cancel_refused_at timestamptz,
    PRIMARY KEY (run_id),
    UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS torve_durable_run_status_created
    ON public.torve_durable_run (status, created_at);
CREATE INDEX IF NOT EXISTS torve_durable_run_created_desc
    ON public.torve_durable_run (created_at DESC, run_id DESC);

CREATE TABLE IF NOT EXISTS public.torve_durable_step (
    run_id text NOT NULL,
    step_id text NOT NULL,
    result jsonb NOT NULL,
    tenant_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, step_id)
);

"""`torve migrate` — owner-grouped, forward-only SQL migrations
(rfcs/MIGRATIONS.md): torve's own document tables, the substrate tables a
forze version dictates, and telemetry from stage 3 onward.

yoyo is an implementation detail behind this module, imported lazily so a
missing `torve[migrate]` extra produces an instruction, not an ImportError
(D-M.3); its checksum verification is what fails an edited-after-apply file
(D-M.5). The `.sql` files ship as package data — a wheel that knows how to
migrate but has nothing to migrate with is discovered at first deployment.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

TARGETS = ("torve", "substrate", "telemetry")
MISSING_EXTRA_EXIT = 3


class MigrateError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def migrations_root() -> Path:
    packaged = Path(str(resources.files("torve"))) / "_migrations"
    if packaged.is_dir():
        return packaged
    development = Path(__file__).resolve().parent.parent.parent / "migrations"
    if development.is_dir():
        return development
    raise MigrateError("torve ships no migrations data — broken installation")


def steps_for(target: str, engine: str = "postgres") -> list[Path]:
    if target not in TARGETS:
        raise MigrateError(f"unknown target {target!r}; one of {', '.join(TARGETS)}")
    directory = migrations_root() / target / engine
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.sql"))


def forze_pin() -> str:
    return (migrations_root() / "substrate" / "FORZE_VERSION").read_text(encoding="utf-8").strip()


def check_forze_pin() -> tuple[bool, str]:
    """(ok, message). The pin is the schema regime the substrate migrations
    were written against; a mismatch is a migration task, not a warning
    (D-M.7)."""
    import importlib.metadata

    pin = forze_pin()
    installed = importlib.metadata.version("forze")
    if pin == installed:
        return True, f"forze {installed} matches the substrate pin"
    return False, (
        f"installed forze {installed} != substrate pin {pin} — a forze upgrade that "
        "changes a substrate schema is a migration task in Torve, not a silent install; "
        "write the migration, verify the conformance battery, then update the pin"
    )


def _yoyo():
    try:
        from yoyo import get_backend, read_migrations
    except ImportError as exc:
        raise MigrateError(
            "yoyo-migrations is not installed — install the extra: pip install 'torve[migrate]'",
            exit_code=MISSING_EXTRA_EXIT,
        ) from exc
    return get_backend, read_migrations


def _yoyo_dsn(dsn: str) -> str:
    """yoyo routes bare postgresql:// through psycopg2; torve ships psycopg 3
    (via forze[postgres]), which yoyo addresses as postgresql+psycopg://."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn.removeprefix("postgresql://")
    return dsn


def apply(target: str, dsn: str) -> int:
    """Apply the target's pending steps; return how many were applied.
    Forward-only by construction (D-M.4): no rollback path exists here."""
    steps = steps_for(target)
    if not steps:
        return 0
    get_backend, read_migrations = _yoyo()
    backend = get_backend(_yoyo_dsn(dsn), migration_table=f"_torve_migrations_{target}")
    migrations = read_migrations(str(steps[0].parent))
    with backend.lock():
        pending = backend.to_apply(migrations)
        backend.apply_migrations(pending)
        return len(pending)


def status(dsn: str | None) -> list[str]:
    """One line per target: available steps, applied count where a database
    is reachable — the first question during a forze upgrade (§5)."""
    lines = []
    for target in TARGETS:
        steps = steps_for(target)
        if not steps:
            lines.append(f"{target:<10} no migrations yet")
            continue
        applied = "database not configured"
        if dsn:
            try:
                get_backend, read_migrations = _yoyo()
                backend = get_backend(_yoyo_dsn(dsn), migration_table=f"_torve_migrations_{target}")
                migrations = read_migrations(str(steps[0].parent))
                pending = len(backend.to_apply(migrations))
                applied = f"{len(steps) - pending}/{len(steps)} applied"
            except MigrateError:
                applied = "yoyo not installed"
        lines.append(f"{target:<10} {len(steps)} step(s), {applied}")
    ok, message = check_forze_pin()
    lines.append(("" if ok else "MISMATCH  ") + message)
    return lines

"""`torve init` — what the code derives, written into `.torve/` (RFC 0057
D-57.5): the JSON Schema of every file torve reads from YAML, named by
the first line of each such file so an editor validates it as it is
typed. Idempotent; never a configuration or a manifest, which are
authored. Phase 2 of RFC 0057 grows it to the contract, the log, the
configuration, the manifest and the ignore file.
"""

from __future__ import annotations

from pathlib import Path

import typer

from torve.cli.console import STYLE_DIM, STYLE_PASS, closing, out
from torve.cli.options import ConfigOption, RootOption, load_config
from torve.domain.states import EXIT_OK

# ----------------------- #


def init_cmd(
    root: RootOption = Path("."),
    config: ConfigOption = None,
) -> None:
    """Write the schemas of the specification's four files into the schemas
    directory beside the corpus, rewriting any that lag the model. Runs
    again without a diff; `spec check` reddens when a schema is stale."""

    from torve.config.spec import schema_file, schema_text
    from torve.domain.spec import FILES

    corpus = root / load_config(root, config).specs.path
    console = out()
    written = 0

    for file_name in FILES:
        path = schema_file(corpus, file_name)
        text = schema_text(file_name)

        if path.is_file() and path.read_text(encoding="utf-8") == text:
            console.print(f"  {path.name}", style=STYLE_DIM)
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        console.print(f"  {path.name}  written", style=STYLE_PASS)
        written += 1

    closing(console, f"{written} schema(s) written under {path.parent}", STYLE_PASS)
    raise typer.Exit(EXIT_OK)

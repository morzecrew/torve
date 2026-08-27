set quiet
set shell := ["bash", "-cu"]

# ----------------------- #
# Paths / constants

_uv_sync := "uv sync --all-groups > /dev/null 2>&1"

# ....................... #

_pwd := justfile_directory()

# ----------------------- #
# Default command

[no-exit-message]
_default:
    echo "Available commands:"
    echo
    just --color=always --list | sed '1d'

help:
    just

# ----------------------- #
# Helpers

# Run a command and print the result based on the output
[no-cd]
_uv_cmd name strict *command:
    @printf "%-30s" "{{ name }}..."

    @out="/tmp/{{ name }}.$$$$" \
    trap 'rm -f "$$out"' EXIT; \
    if uv run {{ command }} >"$$out" 2>&1; then \
        echo "✅"; \
    else \
        echo "❌"; \
        echo ""; \
        cat "$$out"; \
        echo ""; \
        if {{ strict }}; then \
            exit 1; \
        fi; \
    fi

# ----------------------- #
# CI

# Run the test suite
test *args='':
    {{ _uv_sync }}

    uv run pytest {{ args }}

# Run all quality checks
[arg("strict", long, short="s", value="true", help="Enable strict mode (fail on error in any check)")]
quality strict="false":
    {{ _uv_sync }}

    just _uv_cmd "Linting" {{ strict }} ruff check "src"
    just _uv_cmd "Formatting" {{ strict }} ruff format --check "src"
    just _uv_cmd "Types" {{ strict }} mypy "src"
    just _uv_cmd "Imports" {{ strict }} lint-imports
    just _uv_cmd "RFC corpus" {{ strict }} torve rfc check

# ----------------------- #
# Utils

_worktree_dir := join(_pwd, "..", "worktrees")

# Create a worktree for a branch
[arg("new", long, value="true", help="Create a worktree for a new branch")]
worktree branch new="false":
    mkdir -p {{ _worktree_dir }}

    if {{ new }}; then \
        git worktree add {{ _worktree_dir }}/torve-{{ branch }} -b {{ branch }} main;
    else \
        git worktree add {{ _worktree_dir }}/torve-{{ branch }} {{ branch }};
    fi

"""Torve — deterministic gates for agent and human pull requests.

Names resolve from their canonical modules: `torve.gates.runner.run_gates`,
`torve.domain.task.Task`, and so on. There is no re-export table — the
gates-only CI path never imports the runner, the store or an adapter because
nothing here imports them, not because a lazy front door defers them
(S-0015/the-front-door, S-0015/A-2).
"""

__version__ = "0.1.0"

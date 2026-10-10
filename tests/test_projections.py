def _git(root, *args):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )


def _repo(tmp_path):
    """A checkout with a bare `origin`, its identity configured."""

    import subprocess

    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(root, "remote", "add", "origin", str(remote))
    return root


def _landing(root, task, commit, at="2026-09-09T12:00:00Z", document="S-0009"):
    path = root / ".torve" / "specs" / document / "execution"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{task}-1-{at.replace('-', '').replace(':', '')}.yaml").write_text(
        f"task: {task}\nat: '{at}'\ncommit: {commit}\n", encoding="utf-8"
    )


def test_a_task_landed_only_on_a_documents_remote_tip_reads_landed_with_an_empty_stream(tmp_path):
    """S-0099/D-1: the base holds a document's landings only once it merged.
    Before that a landing file lives on the document branch's remote tip, and
    a host whose stream is empty reads it there."""
    from torve.application.projections import landed

    root = _repo(tmp_path)
    _git(root, "commit", "-q", "--allow-empty", "-m", "base")
    _git(root, "checkout", "-q", "-b", "torve/S-0009")
    _landing(root, "T-0001", "a" * 40)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "land T-0001")
    _git(root, "push", "-q", "origin", "torve/S-0009")
    _git(root, "checkout", "-q", "-")

    # Nothing on the base and nothing in the stream: the remote tip answers.
    assert not (root / ".torve" / "telemetry.jsonl").exists()
    assert landed(root) == {"T-0001": "a" * 40}


def test_a_squash_merged_documents_tasks_read_landed_from_the_base(tmp_path):
    """S-0085/D-3 needed the stream for this and no longer does: a squash
    merge puts the landing files on the base tree, and that is the carrier."""
    from torve.application.projections import landed

    root = _repo(tmp_path)
    _landing(root, "T-0001", "b" * 40)
    _landing(root, "T-0002", "c" * 40)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "squash torve/S-0009")

    assert landed(root) == {"T-0001": "b" * 40, "T-0002": "c" * 40}


def test_a_rebase_that_renames_every_sha_leaves_the_answer_unchanged(tmp_path):
    """The landing file carries the logical commit, not the branch sha, so a
    document branch rewritten under a new sha answers the same."""
    from torve.application.projections import landed

    root = _repo(tmp_path)
    _git(root, "commit", "-q", "--allow-empty", "-m", "base")
    _git(root, "checkout", "-q", "-b", "torve/S-0009")
    _landing(root, "T-0001", "a" * 40)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "land T-0001")
    _git(root, "push", "-q", "origin", "torve/S-0009")

    before = landed(root)

    # Rebase the branch onto an amended base: every branch sha is renamed.
    _git(root, "commit", "-q", "--amend", "--allow-empty", "-m", "rebased base")
    _git(root, "push", "-q", "--force", "origin", "torve/S-0009")

    assert _git(root, "rev-parse", "refs/remotes/origin/torve/S-0009").stdout != before["T-0001"]
    assert landed(root) == before == {"T-0001": "a" * 40}


def test_a_wait_on_another_documents_tasks_names_the_document(tmp_path):
    """S-0085/D-6: the reader wants the document whose landing the task waits
    on, not four task ids to resolve by hand."""
    import yaml

    from torve.application.projections import cross_document_waits

    def contract(task_id: str, spec: str, depends_on: tuple[str, ...] = ()) -> None:
        directory = tmp_path / ".torve" / "tasks" / task_id
        directory.mkdir(parents=True)
        (directory / "contract.yaml").write_text(
            yaml.safe_dump({"id": task_id, "spec": spec, "depends_on": list(depends_on)}),
            encoding="utf-8",
        )

    assert cross_document_waits(tmp_path) == {}
    contract("T-0001", "S-0090")
    contract("T-0002", "S-0090")
    contract("T-0003", "S-0092", ("T-0001", "T-0002"))
    contract("T-0004", "S-0092", ("T-0003",))

    # A wait inside the document is not a wait on a document's landing.
    assert cross_document_waits(tmp_path) == {"T-0003": {"S-0090": ["T-0001", "T-0002"]}}
    assert cross_document_waits(tmp_path, {"T-0001"}) == {"T-0003": {"S-0090": ["T-0002"]}}
    assert cross_document_waits(tmp_path, {"T-0001", "T-0002"}) == {}


def test_the_four_old_landing_readers_are_gone():
    """S-0099/D-1: one function answers "is this task landed" for every
    caller, and the four readers it replaced are deleted."""
    from torve.application import projections

    for name in ("lane_landings", "shipped_landings", "shipped_ids", "landed_ids"):
        assert not hasattr(projections, name)

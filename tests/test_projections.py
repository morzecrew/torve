def test_lane_landings_read_the_hosts_own_stream(tmp_path):
    """A task the lane landed onto a document branch is a landing the served
    manager must hear of, or its dependents wait on the board forever
    (bloomery S-0008, 2026-09-19)."""
    from torve.application.projections import lane_landings
    from torve.application.telemetry import engine_event

    assert lane_landings(tmp_path) == {}
    engine_event(tmp_path, "lane_landed", {"task": "T-1", "sha": "a" * 40, "unit": "document"})
    engine_event(tmp_path, "lane_document_branch", {"task": "T-2", "sha": "c" * 40})
    engine_event(tmp_path, "lane_landed", {"task": "T-1", "sha": "b" * 40})
    assert lane_landings(tmp_path) == {"T-1": "b" * 40}


def test_a_documents_landing_is_a_landing_of_every_task_its_branch_carried(tmp_path):
    """A squash-merged document's branch commits are on no base, so the merge
    commit is the landing its tasks are on the base by (S-0085/D-3)."""
    from torve.application.projections import lane_landings
    from torve.application.telemetry import engine_event

    engine_event(tmp_path, "lane_landed", {"task": "T-1", "sha": "a" * 40, "unit": "document"})
    engine_event(tmp_path, "lane_document_landed", {"sha": "m" * 40, "tasks": ["T-1", "T-2"]})

    assert lane_landings(tmp_path) == {"T-1": "m" * 40, "T-2": "m" * 40}

    # Newest wins whichever order the stream holds them in, and a document
    # that carried no tasks stamps nothing.
    engine_event(tmp_path, "lane_landed", {"task": "T-1", "sha": "b" * 40})
    engine_event(tmp_path, "lane_document_landed", {"sha": "n" * 40, "tasks": []})

    assert lane_landings(tmp_path) == {"T-1": "b" * 40, "T-2": "m" * 40}


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

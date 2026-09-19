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

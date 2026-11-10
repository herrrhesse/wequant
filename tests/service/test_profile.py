from wequant.service.profile import Timer


def test_a_stage_runs_from_the_previous_mark_and_repeats_add_up(monkeypatch):
    clock = iter([10.0, 10.5, 11.0, 13.0, 13.25])
    monkeypatch.setattr("wequant.service.profile.time.perf_counter", lambda: next(clock))
    timer = Timer("op")
    for stage in ("a", "b", "a", "c"):
        timer.mark(stage)
    report = timer.report()
    assert [(s.name, s.seconds, s.calls) for s in report.stages] == [("a", 2.5, 2), ("b", 0.5, 1), ("c", 0.25, 1)]
    assert (report.operation, report.seconds) == ("op", 3.25)

import json

import polars as pl
import pytest

from tests.baostock_raw_fake import CONFLICTS, D1, D2, D3, SOURCE, STEADY, World, conflict_world, raw_row
from wequant.data.merge import UnresolvedConflicts, apply_merge, plan_merge
from wequant.data.merge.propose import propose
from wequant.data.merge.resolutions import add


@pytest.fixture
def world(tmp_path):
    return conflict_world().write(tmp_path / "home")


def resolutions(store, raw, path, status="proposed"):
    add(path, [entry | {"status": status} for entry in propose(store, raw)])
    return path


def keys(frame):
    return frame.select("code", "date", "kind").rows()


def test_propose_follows_the_rules(world):
    store, raw = world
    proposals = propose(store, raw)
    assert [(p["code"], p["date"], p["kind"]) for p in proposals] == sorted(CONFLICTS, key=lambda c: (c[1], c[0]))
    assert [(p["take"], p["fields"]) for p in proposals] == [
        ("seed", ("open", "high", "low", "close")),
        ("baostock", ("open", "high", "low", "close")),
        ("seed", ("amount",)),
        ("seed", ("volume", "amount")),
        ("null", ("amount",)),
    ]
    assert {p["status"] for p in proposals} == {"proposed"}
    assert propose(store, raw, days=[D3]) == proposals[-1:]


def test_conflicts_without_an_entry_stop_the_merge_before_anything_is_written(world, tmp_path):
    store, raw = world
    (tmp_path / "empty.toml").write_text("# no decision yet\n", "utf-8")
    plan = plan_merge(store, raw, resolutions=tmp_path / "empty.toml")
    assert keys(plan.unresolved) == CONFLICTS and not plan.unresolved["proposed"].any()
    assert plan.stats["resolutions"] == {"applied": {}, "flagged": 0, "unresolved": 5, "proposed": 0}
    with pytest.raises(UnresolvedConflicts) as stop:
        apply_merge(store, plan)
    assert str(stop.value).splitlines() == [
        "5 conflicts lack an approved resolution (0 proposed, awaiting approval; 5 without an entry):",
        *(f"{code} {day} {kind}: no entry (run propose)" for code, day, kind in CONFLICTS),
    ]
    assert store.manifest()["version"] == 1


def test_proposed_entries_still_stop_the_merge(world, tmp_path):
    store, raw = world
    path = resolutions(store, raw, tmp_path / "r.toml")
    text = path.read_text("utf-8").replace('status = "proposed"', 'status = "approved"', 2)
    path.write_text(text, "utf-8")  # the two price mismatches come first: approved
    plan = plan_merge(store, raw, resolutions=path)
    assert keys(plan.unresolved) == CONFLICTS[1:4] and plan.unresolved["proposed"].all()
    with pytest.raises(UnresolvedConflicts, match="600002.SH 2024-01-03 average_price: a proposed entry awaits"):
        apply_merge(store, plan)
    assert store.manifest()["version"] == 1


def test_approved_entries_are_applied_each_with_one_data_issues_row(world, tmp_path):
    store, raw = world
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")
    plan = plan_merge(store, raw, resolutions=path)
    assert plan.unresolved.is_empty()
    apply_merge(store, plan)

    bars = store.read_table("bars", codes=[c for c, _, _ in CONFLICTS])
    cells = bars.join(pl.DataFrame(CONFLICTS, schema=["code", "date", "kind"], orient="row"), on=["code", "date"])
    assert cells.select("code", "open", "high", "low", "close", "volume", "amount", "source").rows() == [
        ("600000.SH", 10200, 10200, 10200, 10200, 1000, 10200.0, SOURCE),  # the seed's prices
        ("600001.SH", 10300, 10300, 10300, 10300, 1000, 10300.0, SOURCE),  # the seed's amount
        ("600002.SH", 10300, 10300, 10300, 10300, 1000, 10300.0, SOURCE),  # the seed's volume and amount
        ("600003.SH", 5200, 5200, 5200, 5200, 1000, None, SOURCE),  # amount unknown, the bar kept
        ("600004.SH", 10200, 10210, 10200, 10210, 1000, 10200.0, SOURCE),  # BaoStock's prices
    ]

    issues = store.read_table("data_issues").filter(pl.col("action").str.starts_with("resolved_"))
    assert issues.select("code", "date", "kind", "action").rows() == [
        (code, day, kind, f"resolved_{take}")
        for (code, day, kind), take in zip(CONFLICTS, ("seed", "seed", "seed", "null", "baostock"), strict=True)
    ]
    detail = json.loads(issues.filter(pl.col("code") == "600002.SH")["detail"].item())
    assert {k: v for k, v in detail.items() if k != "evidence"} == {
        "fields": ["volume", "amount"],
        "before": {"volume": 1100, "amount": 10300.0},
        "after": {"volume": 1000, "amount": 10300.0},
    }
    assert detail["evidence"].endswith("the volumes differ and the seed bar passes")
    assert json.loads(issues.filter(pl.col("code") == "600003.SH")["detail"].item())["after"] == {"amount": None}

    flagged = store.read_table("data_issues").filter(pl.col("kind") == "close_unconfirmed")
    assert flagged.select("code", "date", "action").rows() == [("600004.SH", D1, "flagged")]
    assert json.loads(flagged["detail"].item()) == {
        "close": 10210,
        "baostock_close": 10210,
        "seed_close": 10200,
        "next_date": "2024-01-03",
        "next_preclose": 10220,
        "ex_event": False,
    }
    assert plan.stats["resolutions"] == {
        "applied": {
            "average_price resolved_null": 1,
            "average_price resolved_seed": 2,
            "price_mismatch resolved_baostock": 1,
            "price_mismatch resolved_seed": 1,
        },
        "flagged": 1,
        "unresolved": 0,
        "proposed": 0,
    }
    again = apply_merge(store, plan_merge(store, raw, resolutions=path))
    assert not again.changed


def test_an_entry_taking_the_seed_needs_a_seed_bar(world, tmp_path):
    store, raw = world
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")
    path.write_text(path.read_text("utf-8").replace('take = "null"', 'take = "seed"'), "utf-8")
    with pytest.raises(ValueError, match=r"600003.SH 2024-01-04 takes the seed's \['amount'\]; the store has none"):
        plan_merge(store, raw, resolutions=path)


def test_a_resolution_may_not_leave_an_amount_outside_low_high(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY)
    raw_row(w, "600000.SH", D1, close="10.3000", high="10.3000", amount="10300.0000")  # 10.30 a share: above 10.20
    store, raw = w.write(tmp_path / "home")
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")  # D2's preclose repeats the seed's close
    with pytest.raises(ValueError, match="amounts outside"):
        plan_merge(store, raw, resolutions=path)


def test_an_adj_factor_step_before_the_reference_flags_the_kept_close(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY, steps={D2: 1.1})
    raw_row(w, "600000.SH", D1, close="10.2100", high="10.2100", amount="10200.0000")
    raw_row(w, "600000.SH", D2, preclose="10.2100")  # repeats BaoStock's close, but the step spoils the comparison
    store, raw = w.write(tmp_path / "home")
    (entry,) = propose(store, raw)
    assert entry["take"] == "baostock" and "an adj_factor step by then spoils the comparison" in entry["evidence"]
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")
    plan = plan_merge(store, raw, resolutions=path)
    flagged = plan.issues.filter(pl.col("kind") == "close_unconfirmed")
    assert json.loads(flagged["detail"].item())["ex_event"] is True

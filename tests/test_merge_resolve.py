import json

import polars as pl
import pytest

from tests.baostock_raw_fake import (
    CONFLICTS,
    D1,
    D2,
    D3,
    D4,
    SOURCE,
    STEADY,
    World,
    conflict_world,
    day_row,
    empty_resolutions,
    raw_row,
    write_day,
)
from wequant.data.merge import UnresolvedConflicts, apply_merge, plan_merge
from wequant.data.merge.propose import propose
from wequant.data.merge.resolutions import add, load

OHLC = ["open", "high", "low", "close"]


@pytest.fixture
def world(tmp_path):
    return conflict_world().write(tmp_path / "home")


def resolutions(store, raw, path, status="proposed"):
    add(path, [entry | {"status": status} for entry in propose(store, raw)])
    return path


def keys(frame):
    return frame.select("code", "date", "kind").rows()


def details(store, code):
    issues = store.read_table("data_issues", codes=[code]).filter(pl.col("action").str.starts_with("resolved_"))
    return [json.loads(d) for d in issues["detail"]]


def test_propose_follows_the_rules(world):
    store, raw = world
    proposals = propose(store, raw)
    assert [(p["code"], p["date"], p["kind"]) for p in proposals] == CONFLICTS
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
    plan = plan_merge(store, raw, resolutions=empty_resolutions(tmp_path))
    assert keys(plan.unresolved) == CONFLICTS and set(plan.unresolved["entry"]) == {"missing"}
    assert plan.stats["resolutions"] == {
        "applied": {},
        "unresolved": {"missing": 5, "unfetched": 0, "proposed": 0, "rejected": 0, "unpriced": 0},
        "idle": [],
    }
    with pytest.raises(UnresolvedConflicts) as stop:
        apply_merge(store, plan)
    assert str(stop.value).splitlines() == [
        "5 conflicts lack an approved resolution (5 missing, 0 unfetched, 0 proposed, 0 rejected, 0 unpriced):",
        *(f"{code} {day} {kind}: no entry (run propose)" for code, day, kind in CONFLICTS),
    ]
    assert store.manifest()["version"] == 1


def test_proposed_entries_still_stop_the_merge(world, tmp_path):
    store, raw = world
    path = resolutions(store, raw, tmp_path / "r.toml")
    text = path.read_text("utf-8").replace('status = "proposed"', 'status = "approved"', 2)
    path.write_text(text, "utf-8")  # the two price mismatches come first: approved
    plan = plan_merge(store, raw, resolutions=path)
    assert keys(plan.unresolved) == CONFLICTS[2:] and set(plan.unresolved["entry"]) == {"proposed"}
    with pytest.raises(UnresolvedConflicts, match="600002.SH 2024-01-03 average_price: a proposed entry awaits"):
        apply_merge(store, plan)
    assert store.manifest()["version"] == 1

    path.write_text(text[: text.rindex("[[resolution]]")], "utf-8")  # 600003.SH's entry, the last, removed
    plan = plan_merge(store, raw, resolutions=path)
    assert keys(plan.unresolved) == [CONFLICTS[4], *CONFLICTS[2:4]]  # the stop lists conflicts without an entry first
    assert plan.unresolved["entry"].to_list() == ["missing", "proposed", "proposed"]


def test_approved_entries_are_applied_each_with_one_data_issues_row(world, tmp_path):
    store, raw = world
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")
    plan = plan_merge(store, raw, resolutions=path)
    assert plan.unresolved.is_empty()
    apply_merge(store, plan)

    bars = store.read_table("bars", codes=[c for c, _, _ in CONFLICTS])
    cells = bars.join(pl.DataFrame(CONFLICTS, schema=["code", "date", "kind"], orient="row"), on=["code", "date"])
    assert cells.select("code", "open", "high", "low", "close", "volume", "amount", "source").sort("code").rows() == [
        ("600000.SH", 10200, 10200, 10200, 10200, 1000, 10200.0, SOURCE),  # the seed's prices
        ("600001.SH", 10300, 10300, 10300, 10300, 1000, 10300.0, SOURCE),  # the seed's amount
        ("600002.SH", 10300, 10300, 10300, 10300, 1000, 10300.0, SOURCE),  # the seed's volume and amount
        ("600003.SH", 5200, 5200, 5200, 5200, 1000, None, SOURCE),  # amount unknown, the bar kept
        ("600004.SH", 10200, 10210, 10200, 10210, 1000, 10200.0, SOURCE),  # BaoStock's prices
    ]

    issues = store.read_table("data_issues").filter(pl.col("action").str.starts_with("resolved_"))
    unverified = {"price_mismatch": "price_unverified", "average_price": "volume_unverified"}  # rule proposals
    assert issues.select("code", "date", "kind", "action").sort("date", "code").rows() == [
        (code, day, unverified[kind], f"resolved_{take}")
        for (code, day, kind), take in zip(CONFLICTS, ("seed", "baostock", "seed", "seed", "null"), strict=True)
    ]
    (detail,) = details(store, "600002.SH")
    assert {k: v for k, v in detail.items() if k != "evidence"} == {
        "fields": ["volume", "amount"],
        "before": {"volume": 1000, "amount": 10300.0},
        "baostock": {"volume": 1100, "amount": 10300.0},
        "after": {"volume": 1000, "amount": 10300.0},
        "category": "rule",
        "level": "suspicious",
        "verifications": [],
    }
    assert detail["evidence"].endswith("the volumes differ and the seed bar passes")
    (detail,) = details(store, "600004.SH")  # the store's prices, which BaoStock's replace, stay on record
    assert {k: detail[k] for k in ("before", "baostock", "after")} == {
        "before": dict.fromkeys(OHLC, 10200),
        "baostock": {"open": 10200, "high": 10210, "low": 10200, "close": 10210},
        "after": {"open": 10200, "high": 10210, "low": 10200, "close": 10210},
    }
    (detail,) = details(store, "600003.SH")
    assert {k: detail[k] for k in ("before", "baostock", "after")} == {
        "before": None,  # no store bar
        "baostock": {"amount": 0.0},
        "after": {"amount": None},
    }

    assert plan.stats["resolutions"] == {
        "applied": {
            "price_unverified resolved_baostock": 1,
            "price_unverified resolved_seed": 1,
            "volume_unverified resolved_null": 1,
            "volume_unverified resolved_seed": 2,
        },
        "unresolved": {"missing": 0, "unfetched": 0, "proposed": 0, "rejected": 0, "unpriced": 0},
        "idle": [],
    }
    again = apply_merge(store, plan_merge(store, raw, resolutions=path))
    assert not again.changed


def test_propose_after_a_merge_finds_the_decided_conflicts_and_adds_nothing(world, tmp_path):
    store, raw = world
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")
    apply_merge(store, plan_merge(store, raw, resolutions=path))
    again = propose(store, raw, entries=load(path))
    assert [(p["code"], p["date"], p["kind"]) for p in again] == [c for c in CONFLICTS if c[0] != "600004.SH"]
    assert add(path, again) == 0  # 600004.SH holds BaoStock's prices now: no conflict


def test_a_merged_null_amount_keeps_its_first_record(tmp_path):
    w = World()
    w.stock("600005.SH", STEADY)
    raw_row(w, "600005.SH", D2, close="10.4000", high="10.4000", amount="0.0000")  # another close, and no amount
    raw_row(w, "600005.SH", D3, preclose="10.4000")  # D3's exchange preclose repeats BaoStock's close
    store, raw = w.write(tmp_path / "home")
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")
    assert [(p["kind"], p["take"]) for p in propose(store, raw)] == [
        ("average_price", "null"),
        ("price_mismatch", "baostock"),
    ]
    apply_merge(store, plan_merge(store, raw, resolutions=path))
    first = details(store, "600005.SH")
    assert [d["before"] for d in first] == [  # by data_issues key: price_unverified, then volume_unverified
        {"open": 10300, "high": 10300, "low": 10300, "close": 10300},
        {"amount": 10300.0},
    ]

    again = propose(store, raw, entries=load(path))  # the store's amount is null now
    assert [(p["kind"], p["take"]) for p in again] == [("average_price", "null")]
    assert again[0]["evidence"].endswith(
        "seed volume 1000 amount none (average none) against [10.30, 10.40]: the seed bar fails the check too, amount null, bar kept"
    )
    assert add(path, again) == 0
    assert not apply_merge(store, plan_merge(store, raw, resolutions=path)).changed
    assert details(store, "600005.SH") == first


def test_a_price_conflict_waits_for_its_reference_day(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY)
    raw_row(w, "600000.SH", D3, close="10.4100", high="10.4100")  # D3 is the last day: no next day yet
    store, raw = w.write(tmp_path / "home")
    assert propose(store, raw) == []
    plan = plan_merge(store, raw, resolutions=empty_resolutions(tmp_path))
    assert plan.unresolved.select("code", "date", "kind", "entry").rows() == [
        ("600000.SH", D3, "price_mismatch", "unfetched")
    ]
    with pytest.raises(UnresolvedConflicts, match="600000.SH 2024-01-04 price_mismatch: no entry yet: propose waits"):
        apply_merge(store, plan)

    write_day(raw, D4, [day_row("600000.SH", D4, 10500, preclose=10410)])
    (entry,) = propose(store, raw, days=[D3])
    assert (
        entry["take"] == "baostock" and "the 2024-01-05 exchange preclose 10.41 repeats BaoStock's" in entry["evidence"]
    )


def test_amounts_are_checked_on_the_prices_the_price_entry_takes(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY)
    raw_row(w, "600000.SH", D1, close="10.3000", high="10.3000", amount="10300.0000")  # 10.30 a share: above 10.20
    store, raw = w.write(tmp_path / "home")
    proposals = propose(store, raw)  # D2's preclose repeats the seed's close
    assert [(p["kind"], p["fields"], p["take"]) for p in proposals] == [
        ("average_price", ("amount",), "seed"),
        ("price_mismatch", ("open", "high", "low", "close"), "seed"),
    ]
    path = tmp_path / "r.toml"
    add(path, [proposals[1] | {"status": "approved"}])
    plan = plan_merge(store, raw, resolutions=path)
    assert plan.unresolved.select("code", "date", "kind", "entry").rows() == [
        ("600000.SH", D1, "average_price", "missing")
    ]

    add(path, [proposals[0] | {"status": "approved"}])
    apply_merge(store, plan_merge(store, raw, resolutions=path))
    bar = store.read_table("bars", start=D1, end=D1, codes=["600000.SH"])
    assert bar.select("high", "close", "amount").row(0) == (10200, 10200, 10200.0)


def test_an_amount_conflict_waits_for_its_cell_s_price_entry(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY)
    prices = dict.fromkeys(("open", "high", "low", "close"), "10.3000")
    raw_row(w, "600000.SH", D1, **prices)  # the amount stays 10200: 10.20 a share, the seed's price
    store, raw = w.write(tmp_path / "home")
    assert [(p["kind"], p["take"]) for p in propose(store, raw)] == [("price_mismatch", "seed")]
    path = resolutions(store, raw, tmp_path / "r.toml")
    plan = plan_merge(store, raw, resolutions=path)
    assert plan.unresolved.select("kind", "entry").rows() == [
        ("price_mismatch", "proposed"),
        ("average_price", "unpriced"),
    ]

    path.write_text(path.read_text("utf-8").replace('status = "proposed"', 'status = "approved"'), "utf-8")
    apply_merge(store, plan_merge(store, raw, resolutions=path))
    bar = store.read_table("bars", start=D1, end=D1, codes=["600000.SH"])
    assert bar.select("high", "close", "amount").row(0) == (10200, 10200, 10200.0)


def test_propose_checks_amounts_on_the_file_s_price_entry_approved_or_not(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY)
    raw_row(w, "600000.SH", D1, **dict.fromkeys(("open", "high", "low", "close"), "10.3000"))  # amount at 10.20
    store, raw = w.write(tmp_path / "home")
    path = resolutions(store, raw, tmp_path / "r.toml")
    path.write_text(path.read_text("utf-8").replace('take = "seed"', 'take = "baostock"'), "utf-8")  # still proposed
    again = propose(store, raw, entries=load(path))
    assert [(p["kind"], p["take"]) for p in again] == [("average_price", "null"), ("price_mismatch", "seed")]
    assert add(path, again) == 1 and load(path)["take"].to_list() == ["null", "baostock"]


def test_an_amount_entry_must_leave_the_amount_inside_low_high(tmp_path):
    w = World()
    w.stock("600000.SH", STEADY)
    next(b for b in w.bars if b["date"] == D1)["amount"] = 0.0
    raw_row(w, "600000.SH", D1, amount="0.0000")
    store, raw = w.write(tmp_path / "home")
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")  # the seed fails too: take null
    path.write_text(path.read_text("utf-8").replace('take = "null"', 'take = "seed"'), "utf-8")
    with pytest.raises(ValueError, match=r"leave amounts outside \[low, high\]; change them: 600000.SH 2024-01-02"):
        plan_merge(store, raw, resolutions=path)


def test_an_entry_taking_the_seed_needs_a_seed_bar(world, tmp_path):
    store, raw = world
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")
    path.write_text(path.read_text("utf-8").replace('take = "null"', 'take = "seed"'), "utf-8")
    with pytest.raises(ValueError, match=r"600003.SH 2024-01-04 takes the seed's \['amount'\]; the store has none"):
        plan_merge(store, raw, resolutions=path)


def test_an_approved_entry_without_a_conflict_is_listed(world, tmp_path):
    store, raw = world
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")
    apply_merge(store, plan_merge(store, raw, resolutions=path))
    assert plan_merge(store, raw, resolutions=path).stats["resolutions"]["idle"] == []  # BaoStock's prices are kept
    text = path.read_text("utf-8")
    flipped = text.replace('take = "baostock"', 'take = "seed"')  # 600004.SH's, after the store took BaoStock's
    path.write_text(flipped, "utf-8")
    assert plan_merge(store, raw, resolutions=path).stats["resolutions"]["idle"] == [
        {"code": "600004.SH", "date": D1, "kind": "price_mismatch", "fields": OHLC, "take": "seed"}
    ]


def test_a_corrected_entry_writes_its_verified_values_and_cites_its_records(world, tmp_path):
    """D9/D11: an error-level entry corrected from verification records keeps its conflict kind; a notice-level one
    is noted only (its own kind, so exclude_day never drops it); both cite their records."""
    store, raw = world
    proposals = propose(store, raw)
    corrected = {"fields": ("close",), "take": "corrected", "values": {"close": 10150}, "level": "error"}
    corrected |= {"verifications": ("v-00000000000000aa",), "status": "approved", "approval": "test"}
    noted = {"level": "notice", "verifications": ("v-00000000000000bb",), "status": "approved", "approval": "test"}
    add(path := tmp_path / "r.toml", [proposals[0] | corrected, proposals[1] | noted, *proposals[2:]])
    text = path.read_text("utf-8").replace('status = "proposed"', 'status = "approved"')
    path.write_text(text.replace('approval = ""', 'approval = "test"'), "utf-8")
    apply_merge(store, plan_merge(store, raw, resolutions=path))
    assert store.read_table("bars", codes=["600000.SH"]).filter(pl.col("date") == D1)["close"].item() == 10150
    issues = store.read_table("data_issues").filter(pl.col("code").is_in(["600000.SH", "600004.SH"]))
    written = issues.filter(pl.col("action").str.starts_with("resolved_"))
    assert written.select("code", "kind", "action").sort("code").rows() == [
        ("600000.SH", "price_mismatch", "resolved_corrected"),
        ("600004.SH", "price_noted", "resolved_baostock"),
    ]
    cited = [json.loads(d)["verifications"] for d in written.sort("code")["detail"]]
    assert cited == [["v-00000000000000aa"], ["v-00000000000000bb"]]


def test_a_rejected_entry_stops_the_merge_as_rejected(world, tmp_path):
    store, raw = world
    path = resolutions(store, raw, tmp_path / "r.toml", status="approved")
    path.write_text(path.read_text("utf-8").replace('status = "approved"', 'status = "rejected"', 1), "utf-8")
    plan = plan_merge(store, raw, resolutions=path)
    assert plan.unresolved.select("code", "entry").rows() == [("600000.SH", "rejected")]

"""Verification records (D20): append-only under the data root, identified by content, every fault located."""

import pytest

from wequant.data.verifications import Verifications, checked, record_id
from wequant.strategy import SpecErrors

RECORD = {
    "object": {"table": "bars", "code": "600822.SH", "date": "2016-12-01", "fields": ["close"]},
    "values": {"baostock": {"close": 16.71}, "seed": {"close": 16.70}, "exchange": {"close": 16.70}},
    "sources": [{"name": "exchange", "type": "exchange", "url": "https://example.org/k", "fetched_at": "2026-11-10"}],
    "verifier": {"kind": "agent", "name": "checker", "model": "a model"},
    "method": "the exchange's daily bar",
    "conclusion": "the seed's close is the exchange's",
    "correct": {"close": 16.70},
    "level": "error",
}
OTHER = {**RECORD, "correct": {"close": 16.71}, "conclusion": "BaoStock's close is right"}


def test_the_same_record_is_stored_once_under_one_id(tmp_path):
    store = Verifications(tmp_path)
    added, existing = store.submit([RECORD])
    again = store.submit([RECORD])
    assert (added, existing) == ([record_id(checked(RECORD))], []) and again == ([], added)
    assert len(store.read()) == 1


def test_each_submission_is_a_new_file_and_old_files_never_change(tmp_path):
    store = Verifications(tmp_path)
    store.submit([RECORD])
    (first,) = tmp_path.glob("*.jsonl")
    before = first.read_bytes()
    store.submit([OTHER, RECORD])
    assert len(list(tmp_path.glob("*.jsonl"))) == 2 and first.read_bytes() == before
    assert [v.record["correct"] for v in store.read()] == [{"close": 16.70}, {"close": 16.71}]


def test_every_fault_of_a_record_is_located():
    bad = {
        **RECORD,
        "values": {"tencent": {"close": 1.0}},
        "sources": [{**RECORD["sources"][0], "type": "website"}],
        "correct": {"volume": 1},
        "level": "fine",
    }
    with pytest.raises(SpecErrors) as raised:
        checked(bad)
    assert [(e.code, e.loc) for e in raised.value.errors] == [
        ("not_a_choice", ("sources", 0, "type")),
        ("bad_ref", ("values", "tencent")),
        ("unknown_key", ("correct", "volume")),
        ("not_a_choice", ("level",)),
    ]

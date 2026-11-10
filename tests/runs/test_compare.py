"""How archived and fresh bytes differ: JSON leaves, frame cells, whole files and the code notes."""

import datetime as dt

import polars as pl
import pytest

from tests.runs.engines import DESC, hold
from wequant.data.files import parquet_bytes
from wequant.runs import SAME, Comparison, code_notes, differences, frame_differences, json_differences


def test_json_differences_golden():
    old, new = {"a": 1, "b": [1, 2], "c": {"d": None}}, {"a": 1.0, "b": [1, 2, 3], "c": {}}
    assert json_differences(old, new) == ["a: 1 → 1.0", "b: [1, 2] → [1, 2, 3]", "c.d: None → absent"]


def test_json_differences_paths():
    old = {"m": {"x": [{"v": 0.0}, {"v": 2}]}, "z": "same", "new": None}
    new = {"m": {"x": [{"v": -0.0}, {"v": True}]}, "z": "same", "gone": 1}
    assert json_differences(old, new) == [
        "gone: absent → 1",
        "m.x[0].v: 0.0 → -0.0",
        "m.x[1].v: 2 → True",
        "new: None → absent",
    ]
    assert json_differences([1, 2], [1, 3]) == ["[1]: 2 → 3"]
    assert json_differences([], [["st_unknown", "x"]]) == ["[] → [['st_unknown', 'x']]"]
    assert json_differences({"a": [1, {"b": "中"}]}, {"a": [1, {"b": "中"}]}) == []


def test_frame_differences_name_the_first_row_of_each_column():
    old = pl.DataFrame({"date": [dt.date(2024, 1, 2), dt.date(2024, 1, 3)], "x": [1.0, None], "s": ["a", "b"]})
    new = pl.DataFrame({"date": [dt.date(2024, 1, 2), dt.date(2024, 1, 4)], "x": [2.0, 3.0], "s": ["a", "b"]})
    assert frame_differences(old, new) == ["date row 1: 2024-01-03 → 2024-01-04", "x row 0: 1.0 → 2.0 and 1 more rows"]
    assert frame_differences(old, old.clone()) == []


def test_frame_differences_of_shape():
    old = pl.DataFrame({"x": [1.0, 2.0]})
    assert frame_differences(old, old.head(1)) == ["rows 2 → 1"]
    assert frame_differences(old, old.cast({"x": pl.Float32})) == ["columns [('x', Float64)] → [('x', Float32)]"]
    assert frame_differences(old, old.rename({"x": "y"})) == ["columns [('x', Float64)] → [('y', Float64)]"]


def test_signed_zero_is_the_same_content():
    a, b = pl.DataFrame({"x": [0.0, 1.0]}), pl.DataFrame({"x": [-0.0, 1.0]})
    assert parquet_bytes(a) != parquet_bytes(b)
    assert frame_differences(a, b) == []
    assert differences({"x.parquet": parquet_bytes(a)}, {"x.parquet": parquet_bytes(b)}) == {"x.parquet": SAME}


def test_differences_of_whole_files():
    frame = parquet_bytes(pl.DataFrame({"x": [1.0]}))
    old = {"a.json": b'{"k": 1}\n', "b.parquet": frame, "c.json": b"[]\n"}
    new = {"a.json": b'{"k": 1}\n', "b.parquet": frame, "d.json": b"[]\n"}
    assert differences(old, new) == {"c.json": "not produced by the re-run", "d.json": "not archived"}
    assert differences(old, old) == {}
    assert differences({"a.json": b'{"k":1}'}, {"a.json": b'{"k": 1}\n'}) == {"a.json": SAME}


def test_more_than_three_differences_are_counted():
    old = pl.DataFrame({name: [0] for name in "abcde"})
    new = pl.DataFrame({name: [1] for name in "abcde"})
    text = differences({"f.parquet": parquet_bytes(old)}, {"f.parquet": parquet_bytes(new)})["f.parquet"]
    assert text == "a row 0: 0 → 1; b row 0: 0 → 1; c row 0: 0 → 1 (+2 more)"
    metrics = differences({"m.json": b'{"a": 1, "b": 2, "c": 3}'}, {"m.json": b'{"a": 2, "b": 3, "c": 4}'})
    assert metrics == {"m.json": "a: 1 → 2; b: 2 → 3; c: 3 → 4"}


@pytest.mark.parametrize(
    "then, now, notes",
    [
        ("1+ga", "1+ga", ()),
        ("1+ga", "1+gb", ("archived with code 1+ga, re-run with 1+gb",)),
        ("1+ga.dirty", "1+ga.dirty", ("code 1+ga.dirty had uncommitted changes",)),
        (
            "1+ga.dirty",
            "1+gb",
            ("archived with code 1+ga.dirty, re-run with 1+gb", "code 1+ga.dirty had uncommitted changes"),
        ),
        (
            "1+ga",
            "1+gb.dirty",
            ("archived with code 1+ga, re-run with 1+gb.dirty", "code 1+gb.dirty had uncommitted changes"),
        ),
        (
            "1+ga.dirty",
            "1+gb.dirty",
            (
                "archived with code 1+ga.dirty, re-run with 1+gb.dirty",
                "code 1+ga.dirty had uncommitted changes",
                "code 1+gb.dirty had uncommitted changes",
            ),
        ),
    ],
)
def test_code_notes(then, now, notes):
    assert code_notes(then, now) == notes


def test_a_comparison_is_identical_without_differences():
    run = hold(DESC, 1)
    assert Comparison(run.id, run, {}, ("a note",)).identical
    assert not Comparison(run.id, run, {"daily.parquet": SAME}, ()).identical

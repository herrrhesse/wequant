import datetime as dt
from pathlib import Path

import pytest

from wequant.data.merge.resolutions import HEADER, SCHEMA, add, frame, load, render

OHLC = ["open", "high", "low", "close"]
EARLY = {
    "code": "600000.SH",
    "date": dt.date(2023, 11, 7),
    "kind": "price_mismatch",
    "fields": OHLC,
    "take": "baostock",
    "values": {},
    "category": "test",
    "level": "notice",
    "verifications": ("v-0000000000000001",),
    "evidence": 'BaoStock close 9.71, seed close 9.70; the "2023-11-08" preclose repeats BaoStock\'s',
    "status": "approved",
    "approval": "user, by rule",
}
LATE = EARLY | {"code": "000001.SZ", "date": dt.date(2024, 1, 2), "kind": "average_price", "fields": ["amount"]}
LATE |= {"take": "null", "status": "proposed", "approval": "", "verifications": ()}
CORRECTED = EARLY | {"kind": "average_price", "fields": ["volume"], "take": "corrected", "values": {"volume": 19491483}}


def write(path, *entries, header="# decisions\n"):
    path.write_text(header + "".join("\n" + render(e) for e in entries), "utf-8")
    return path


SAMPLE = Path(__file__).parent / "data" / "resolutions_sample.toml"


def test_the_synthetic_sample_loads():
    """The sample shows the format with each (kind, take) kind of entry; it starts with HEADER, the format's comment."""
    entries = load(SAMPLE)
    assert entries.schema == SCHEMA and entries.select("kind", "take").rows() == [
        ("price_mismatch", "seed"),
        ("average_price", "seed"),
        ("average_price", "null"),
    ]
    assert SAMPLE.read_text("utf-8").startswith(HEADER.rstrip())


def test_entries_load_as_written(tmp_path):
    entries = load(write(tmp_path / "r.toml", CORRECTED, EARLY, LATE))  # load parses render's TOML
    assert entries.equals(frame([CORRECTED, EARLY, LATE]))


MALFORMED = {
    "fields_not_allowed": {"take": "seed", "kind": "average_price", "fields": OHLC},  # FIELDS per (kind, take)
    "status": {"status": "done"},
    "values_beside_baostock": {"values": {"close": 9700}},
    "corrected_without_evidence": {
        "take": "corrected",
        "values": {"close": 9700},
        "fields": ["close"],
        "verifications": (),
    },
    "corrected_values_beyond_fields": {"take": "corrected", "values": {"close": 9700, "open": 1}, "fields": ["close"]},
    "undetermined_approved": {"take": "undetermined"},
    "level": {"level": "warn"},
    "evidence": {"evidence": " "},
    "code": {"code": "600000"},
    "date": {"date": "2023-11-07"},
}


@pytest.mark.parametrize("change", MALFORMED.values(), ids=MALFORMED)
def test_a_malformed_entry_is_refused(tmp_path, change):
    with pytest.raises(ValueError):
        load(write(tmp_path / "r.toml", EARLY | change))


def test_keys_must_be_exactly_the_format_s(tmp_path):
    path = tmp_path / "r.toml"
    for text in (render(EARLY) + 'note = "x"\n', render(EARLY).replace('status = "approved"\n', "")):
        path.write_text(text, "utf-8")
        with pytest.raises(ValueError, match="exactly the keys"):
            load(path)


def test_entries_are_sorted_and_unique(tmp_path):
    with pytest.raises(ValueError, match="sorted"):
        load(write(tmp_path / "r.toml", LATE, EARLY))
    with pytest.raises(ValueError, match="repeat"):
        load(write(tmp_path / "r.toml", EARLY, EARLY | {"take": "seed"}))


def test_add_keeps_existing_entries_and_inserts_missing_ones_in_order(tmp_path):
    path = write(tmp_path / "r.toml", LATE, header="# decisions\n# kept as written\n")
    edited = path.read_text("utf-8").replace('status = "proposed"', 'status = "approved"  # by the user')
    path.write_text(edited, "utf-8")
    middle = EARLY | {"date": dt.date(2023, 12, 1), "status": "proposed"}
    assert add(path, [middle | {"take": "seed"}, EARLY | {"status": "proposed"}, LATE | {"take": "seed"}]) == 2
    text = path.read_text("utf-8")
    assert text.startswith("# decisions\n# kept as written\n\n[[resolution]]")
    assert 'status = "approved"  # by the user' in text  # LATE was there: its text is untouched
    assert load(path).select("date", "take", "status").rows() == [
        (dt.date(2023, 11, 7), "baostock", "proposed"),
        (dt.date(2023, 12, 1), "seed", "proposed"),
        (dt.date(2024, 1, 2), "null", "approved"),
    ]
    assert add(path, [middle]) == 0 and path.read_text("utf-8") == text


def test_add_starts_a_new_file_with_the_header(tmp_path):
    path = tmp_path / "new.toml"
    assert add(path, [LATE]) == 1
    assert path.read_text("utf-8").startswith(HEADER.rstrip()) and load(path).height == 1


@pytest.mark.parametrize("opening", ["[[ resolution ]]", "note = 1\n[[resolution]]"])
def test_an_entry_the_split_would_miss_is_refused(tmp_path, opening):
    path = tmp_path / "r.toml"
    path.write_text("# decisions\n" + render(EARLY).replace("[[resolution]]", opening), "utf-8")
    with pytest.raises(ValueError, match="only comments may precede the first entry"):
        load(path)
    with pytest.raises(ValueError, match="only comments may precede the first entry"):
        add(path, [LATE])


def test_a_missing_file_holds_no_decisions(tmp_path):
    """D20: the decisions live in the data root; without its file every conflict stops the merge, as before."""
    assert load(tmp_path / "resolutions" / "baostock.toml").is_empty()

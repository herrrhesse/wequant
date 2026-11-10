import datetime as dt
import re
import tomllib

import pytest

from wequant.data.merge.resolutions import PACKAGED, SCHEMA, add, load, render

OHLC = ["open", "high", "low", "close"]
EARLY = {
    "code": "603081.SH",
    "date": dt.date(2023, 11, 7),
    "kind": "price_mismatch",
    "fields": OHLC,
    "take": "baostock",
    "evidence": 'BaoStock close 9.71, seed close 9.70; the "2023-11-08" preclose repeats BaoStock\'s',
    "status": "approved",
}
LATE = EARLY | {"code": "000001.SZ", "date": dt.date(2024, 1, 2), "kind": "average_price", "fields": ["amount"]}
LATE |= {"take": "null", "status": "proposed"}


def write(path, *entries, header="# decisions\n"):
    path.write_text(header + "".join("\n" + render(e) for e in entries), "utf-8")
    return path


def test_the_packaged_file_loads():
    assert load().schema == SCHEMA
    assert "[[resolution]]" in PACKAGED.read_text("utf-8")  # the format, in the header comment


def test_entries_load_as_written(tmp_path):
    entries = load(write(tmp_path / "r.toml", EARLY, LATE))
    assert entries.to_dicts() == [EARLY, LATE]


def test_render_is_valid_toml():
    assert tomllib.loads(render(EARLY))["resolution"] == [EARLY]


MALFORMED = [
    {"kind": "volume_mismatch"},
    {"take": "seed", "kind": "average_price", "fields": OHLC},
    {"take": "null", "kind": "price_mismatch"},
    {"take": "baostock", "kind": "average_price", "fields": ["amount"]},
    {"fields": ["close"]},
    {"status": "rejected"},
    {"evidence": " "},
    {"code": "600000"},
    {"date": "2023-11-07"},
]


@pytest.mark.parametrize("change", MALFORMED)
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


def test_add_starts_a_new_file_with_the_packaged_header(tmp_path):
    path = tmp_path / "new.toml"
    assert add(path, [LATE]) == 1
    header = re.split(r"(?m)^\[\[resolution\]\]", PACKAGED.read_text("utf-8"))[0]
    assert path.read_text("utf-8").startswith(header.rstrip()) and load(path).height == 1


@pytest.mark.parametrize("opening", ["[[ resolution ]]", "  [[resolution]]", "note = 1\n[[resolution]]"])
def test_an_entry_the_split_would_miss_is_refused(tmp_path, opening):
    path = tmp_path / "r.toml"
    path.write_text("# decisions\n" + render(EARLY).replace("[[resolution]]", opening), "utf-8")
    with pytest.raises(ValueError, match="only comments may precede the first entry"):
        load(path)
    with pytest.raises(ValueError, match="only comments may precede the first entry"):
        add(path, [LATE])

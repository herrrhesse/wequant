import datetime as dt
import hashlib
from fractions import Fraction
from importlib.resources import files

import pytest

from wequant.data.codes import BOARDS
from wequant.rules.book import Band, default_book, load_book

LAUNCH = {"main": dt.date(1996, 12, 16), "chinext": dt.date(2009, 10, 30), "star": dt.date(2019, 7, 22)}
LAUNCH["bse"] = dt.date(2020, 7, 27)

MINIMAL = """
[[band]]
id = "band-a"
board = "main"
since = 2000-01-04
normal = 0.10
st = 0.05

[[band]]
id = "band-b"
board = "main"
since = 2010-01-04
normal = 0.10

[[new_listing]]
id = "ipo-a"
board = "main"
since = 2000-01-04
free_days = 0
first_day_up = 0.44
first_day_down = 0.36

[[lot]]
id = "lot-a"
board = "main"
since = 2000-01-04
min_qty = 100
step = 100
tick = 10

[[stamp_tax]]
id = "stamp-a"
since = 2000-01-04
buy = 0
sell = 0.001

[[transfer_fee]]
id = "transfer-a"
exchanges = ["SH", "SZ"]
since = 2000-01-04
rate = 0.00002
basis = "amount"
confirm = "to check"
"""


def book_from(tmp_path, text):
    path = tmp_path / "rules.toml"
    path.write_text(text, "utf-8")
    return load_book(path)


def test_a_minimal_book_loads(tmp_path):
    book = book_from(tmp_path, MINIMAL)
    assert book.band[0] == Band("band-a", "main", dt.date(2000, 1, 4), 1000, 500)
    assert book.band[1].st_bp == 1000
    assert (book.new_listing[0].first_up_bp, book.new_listing[0].first_down_bp) == (4400, 3600)
    stamp, transfer = book.stamp_tax[0], book.transfer_fee[0]
    assert (stamp.buy, stamp.sell, stamp.exchanges, stamp.basis) == (0, Fraction(1, 1000), ("SH", "SZ", "BJ"), "amount")
    assert transfer.buy == transfer.sell == Fraction(1, 50000)
    assert [row.id for row in book.unconfirmed()] == ["transfer-a"]
    assert book.digest == hashlib.sha256(MINIMAL.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "old, new, named",
    [
        ("tick = 10", "tick = 10\nsize = 1", "lot lot-a"),
        ("since = 2010-01-04", "since = 2000-01-04", "band band-b"),
        ('id = "ipo-a"', 'id = "band-a"', "new_listing band-a"),
        ("normal = 0.10\nst", "normal = 0.10005\nst", "band band-a"),
        ("first_day_down = 0.36\n", "", "new_listing ipo-a"),
        ('basis = "amount"\n', "", "transfer_fee transfer-a"),
        ('board = "main"\nsince = 2000-01-04\nnormal', 'board = "mars"\nsince = 2000-01-04\nnormal', "band band-a"),
        ("free_days = 0", "free_days = 1", "new_listing ipo-a"),
        ("step = 100", "step = 200", "lot lot-a"),
        ("sell = 0.001", "sell = 1.5", "stamp_tax stamp-a"),
    ],
)
def test_invalid_rows_name_their_table_and_id(tmp_path, old, new, named):
    assert old in MINIMAL
    with pytest.raises(ValueError, match=named):
        book_from(tmp_path, MINIMAL.replace(old, new, 1))


def test_unknown_tables_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="commission"):
        book_from(tmp_path, MINIMAL + '\n[[commission]]\nid = "c"\n')


def test_in_force_picks_the_latest_row_since_day(tmp_path):
    book = book_from(tmp_path, MINIMAL)
    assert book.in_force("band", "main", dt.date(2010, 1, 1)).id == "band-a"
    assert book.in_force("band", "main", dt.date(2010, 1, 4)).id == "band-b"
    assert book.in_force("transfer_fee", "SZ", dt.date(2024, 1, 2)).id == "transfer-a"
    with pytest.raises(LookupError):
        book.in_force("band", "main", dt.date(2000, 1, 3))
    with pytest.raises(LookupError):
        book.in_force("lot", "star", dt.date(2024, 1, 2))


def test_frames_are_ready_for_join_asof(tmp_path):
    frame = book_from(tmp_path, MINIMAL).frame("band")
    assert frame.columns == ["board", "since", "band_id", "normal_bp", "st_bp"]
    assert frame["band_id"].to_list() == ["band-a", "band-b"]
    with pytest.raises(ValueError):
        book_from(tmp_path, MINIMAL).frame("stamp_tax")


def test_the_packaged_book_is_loaded_once():
    data = files("wequant.rules").joinpath("rules.toml").read_bytes()
    assert default_book() is default_book()
    assert default_book().digest == hashlib.sha256(data).hexdigest()


def test_every_board_has_a_band_lot_and_listing_rule_from_its_launch_through_2026():
    book = default_book()
    for board in BOARDS:
        for day in (LAUNCH[board], dt.date(2026, 12, 31)):
            assert {book.in_force(table, board, day).board for table in ("band", "new_listing", "lot")} == {board}


def test_stamp_tax_and_transfer_fee_cover_sh_and_sz_from_2000():
    book = default_book()
    for exchange in ("SH", "SZ"):
        for table in ("stamp_tax", "transfer_fee"):
            assert exchange in book.in_force(table, exchange, dt.date(2000, 1, 4)).exchanges


def test_uncertain_rules_carry_confirm_notes():
    unconfirmed = {row.id for row in default_book().unconfirmed()}
    assert {"band-main-2026", "ipo-main-2014", "transfer-sh-par-1990", "transfer-2015"} <= unconfirmed
    assert "band-main-1996" not in unconfirmed

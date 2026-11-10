import datetime as dt
import hashlib
from fractions import Fraction

import pytest

from wequant.data.codes import BOARDS
from wequant.rules.book import Band, Rate, RuleBook, default_book, fees_in_force, load_book

LAUNCH = {
    "main": dt.date(1996, 12, 16),
    "chinext": dt.date(2009, 10, 30),
    "star": dt.date(2019, 7, 22),
    "bse": dt.date(2020, 7, 27),
}

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
        ("normal = 0.10\nst", "normal = 0.10005\nst", "band band-a"),
    ],
)
def test_invalid_rows_name_their_table_and_id(tmp_path, old, new, named):
    assert old in MINIMAL
    with pytest.raises(ValueError, match=named):
        book_from(tmp_path, MINIMAL.replace(old, new, 1))


def test_in_force_picks_the_latest_row_since_day(tmp_path):
    book = book_from(tmp_path, MINIMAL)
    assert book.in_force("band", "main", dt.date(2010, 1, 1)).id == "band-a"
    assert book.in_force("band", "main", dt.date(2010, 1, 4)).id == "band-b"
    assert book.in_force("transfer_fee", "SZ", dt.date(2024, 1, 2)).id == "transfer-a"
    with pytest.raises(LookupError):
        book.in_force("band", "main", dt.date(2000, 1, 3))
    with pytest.raises(LookupError):
        book.in_force("lot", "star", dt.date(2024, 1, 2))


def test_every_board_has_a_band_lot_and_listing_rule_from_its_launch_through_2026():
    book = default_book()
    for board in BOARDS:
        for day in (LAUNCH[board], dt.date(2026, 12, 31)):
            assert {book.in_force(table, board, day).board for table in ("band", "new_listing", "lot")} == {board}
    for exchange in ("SH", "SZ"):
        for table in ("stamp_tax", "transfer_fee"):
            assert exchange in book.in_force(table, exchange, dt.date(2000, 1, 4)).exchanges


def test_the_fee_rules_in_force_over_a_window_end_where_the_next_rule_for_their_exchanges_starts():
    def rate(id, exchanges, since):
        return Rate(id, exchanges, dt.date.fromisoformat(since), Fraction(1, 1000), Fraction(1, 1000), "amount")

    book = RuleBook(
        "digest",
        (),
        (),
        (),
        (rate("s1", ("SH", "SZ"), "2020-01-01"), rate("s2", ("SH", "SZ"), "2024-02-01")),
        (
            rate("t-sh", ("SH",), "2019-01-01"),
            rate("t-sz", ("SZ",), "2019-01-01"),
            rate("t", ("SH", "SZ"), "2025-01-01"),
        ),
    )
    found = [
        (table, rule.id, until) for table, rule, until in fees_in_force(book, dt.date(2024, 1, 15), dt.date(2024, 3, 1))
    ]
    assert found == [
        ("stamp_tax", "s1", dt.date(2024, 1, 31)),
        ("stamp_tax", "s2", None),
        ("transfer_fee", "t-sh", None),
        ("transfer_fee", "t-sz", None),
    ]
    assert [rule.id for _, rule, _ in fees_in_force(book, dt.date(2023, 1, 1), dt.date(2023, 6, 1))] == [
        "s1",
        "t-sh",
        "t-sz",
    ]

"""Stock pools (round 3): reading a pasted list or file without losing an item, and the pool files."""

import pytest

from wequant.pools import NoPool, PoolBook, parse
from wequant.strategy import SpecError

STOCKS = {  # code -> current name, as the data has them
    "600519.SH": "贵州茅台",
    "000001.SZ": "平安银行",
    "600000.SH": "浦发银行",
    "601111.SH": "同名",
    "000111.SZ": "同名",
}


@pytest.mark.parametrize("item", ["600519", "600519.SH", "600519.sh", "sh600519", "SH600519", "sh.600519", "贵州茅台"])
def test_a_code_in_any_common_form_or_a_name_finds_the_stock(item):
    assert parse(item, STOCKS).codes == ["600519.SH"]


def test_every_item_is_found_repeated_or_reported_with_its_reason():
    """Nothing is dropped silently: each item lands in exactly one of the three lists."""
    text = "代码,名称\n600519,贵州茅台\n000001.SZ\t浦发银行 同名;430047 000300.SH 999999 不存在"
    parsed = parse(text, STOCKS)
    reported = {u.item: (u.reason, u.candidates) for u in parsed.unparsed}
    assert parsed.codes == ["600519.SH", "000001.SZ", "600000.SH"] and parsed.duplicates == ["贵州茅台"]
    assert reported == {
        "代码": ("header", ()),
        "名称": ("header", ()),
        "同名": ("ambiguous", ("000111.SZ", "601111.SH")),
        "430047": ("beijing", ()),
        "000300.SH": ("not_a_stock", ()),
        "999999": ("not_found", ()),
        "不存在": ("not_found", ()),
    }


def test_a_saved_pool_reads_back_and_a_resave_keeps_its_id_and_creation(tmp_path):
    stamps = iter(["2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"])
    book = PoolBook(tmp_path, clock=lambda: next(stamps))
    first = book.save("自选", ["600519.SH", "000001.SZ"])
    second = book.save("改名", ["000001.SZ"], first.id)
    assert book.load(first.id) == second and book.pools() == [second]
    assert (second.id, second.created_at, second.modified_at) == (
        first.id,
        first.created_at,
        "2026-02-01T00:00:00+00:00",
    )


def test_a_deleted_or_unknown_pool_is_not_found(tmp_path):
    book = PoolBook(tmp_path)
    pool = book.save("自选", ["600519.SH"])
    book.delete(pool.id)
    with pytest.raises(NoPool):
        book.load(pool.id)
    with pytest.raises(NoPool):
        book.save("自选", [], "p-00000000")


def test_a_pool_refuses_a_repeated_code(tmp_path):
    with pytest.raises(SpecError) as raised:
        PoolBook(tmp_path).save("自选", ["600519.SH", "600519.SH"])
    assert (raised.value.code, raised.value.loc) == ("duplicate_value", ("codes", 1))

"""Properties of the comparisons: JSON differences agree with document bytes, and a changed cell is named."""

import datetime as dt

import polars as pl
from hypothesis import assume, given
from hypothesis import strategies as st

from wequant.runs import document, frame_differences, json_differences

SCALARS = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text()
PLAIN = st.recursive(
    SCALARS, lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=3), inner, max_size=4)
)
VALUES = {
    "date": st.dates(dt.date(2000, 1, 1), dt.date(2030, 12, 31)),
    "code": st.text(max_size=4),
    "x": st.floats(allow_nan=False),
}
SCHEMA = {"date": pl.Date, "code": pl.String, "x": pl.Float64}


def frame_of(height: int) -> st.SearchStrategy[pl.DataFrame]:
    columns = {name: st.lists(st.none() | values, min_size=height, max_size=height) for name, values in VALUES.items()}
    return st.fixed_dictionaries(columns).map(lambda data: pl.DataFrame(data, schema=SCHEMA))


FRAMES = st.integers(1, 6).flatmap(frame_of)


@given(PLAIN, PLAIN)
def test_json_differences_are_empty_exactly_when_documents_are_equal(a, b):
    assert (json_differences(a, b) == []) == (document(a) == document(b))


@given(FRAMES, st.data())
def test_one_changed_cell_is_named(frame, data):
    name = data.draw(st.sampled_from(frame.columns))
    row = data.draw(st.integers(0, frame.height - 1))
    old, new = frame[name][row], data.draw(st.none() | VALUES[name])
    assume((old is None) != (new is None) or (old is not None and old != new))  # 0.0 and -0.0 are the same content
    values = frame[name].to_list()
    values[row] = new
    found = frame_differences(frame, frame.with_columns(pl.Series(name, values, dtype=SCHEMA[name])))
    assert len(found) == 1 and found[0].startswith(f"{name} row {row:,}: ") and "more rows" not in found[0]

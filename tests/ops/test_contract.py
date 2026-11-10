"""Operator contracts: no look-ahead, bars rather than rows, ±inf as NaN, any layout, untouched inputs, validation,
windows that forget older bars, and kernels loaded from numba's cache."""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.ops.panels import argument, cases, rngs
from tests.ops.reference import ELEMENTWISE, REGISTRY, ROLES
from wequant.ops import REF, SMA

EXAMPLES = settings(max_examples=settings().max_examples // 2)
NAMES = list(REGISTRY)
SECTION = [name for name, op in REGISTRY.items() if op.kind == "section"]
FLOAT_ONLY = [name for name, op in REGISTRY.items() if op.kind not in ELEMENTWISE]
WINDOWED = [name for name, op in REGISTRY.items() if op.minimum is not None]
ANCHORED = {"EMA", "SMA"}  # windowed, yet every bar since the grid's first row weighs in
# Elementwise operators share elementwise()/defined(); one per shape (IF has its own path) stands for them all.
ELEMENTWISE_SAMPLE = {"ABS", "DIV", "BETWEEN", "IF"}
TIME_SERIES = [name for name in FLOAT_ONLY if name not in SECTION]
GRID = np.array([[1.0, 4.0], [2.0, 3.0], [5.0, 3.0], [4.0, 1.0], [6.0, 2.0], [3.0, 5.0]])
SQUARE = np.array([[1.0, 4.0, 2.0], [5.0, 3.0, 3.0], [4.0, 1.0, 6.0]])


def identical(a, b) -> bool:
    """The same shape and the same bits in every cell."""
    return a.shape == b.shape and np.array_equal(a.view(np.uint64), b.view(np.uint64))


def on_grids(f, arguments) -> tuple:
    """f applied to each grid argument; scalars unchanged."""
    return tuple(f(a) if np.ndim(a) else a for a in arguments)


def draw_case(data, name, **options):
    """The operator with its drawn window bound, and its drawn grid and scalar arguments."""
    op = REGISTRY[name]
    arguments, window = data.draw(cases(ROLES[op.kind], op.minimum, **options))
    return lambda *args: op.call(*args, **window), arguments


@pytest.mark.parametrize("name", [name for name in NAMES if name in FLOAT_ONLY or name in ELEMENTWISE_SAMPLE])
@EXAMPLES
@given(data=st.data())
def test_the_result_depends_only_on_the_data_up_to_each_row(name, data):
    call, arguments = draw_case(data, name, min_rows=1)
    before, expected = on_grids(np.copy, arguments), call(*arguments)
    t, rng = data.draw(st.integers(0, len(arguments[0]) - 1)), data.draw(rngs())
    assert_prefix(call, arguments, expected, t)
    assert_poison(call, poison(data, name, arguments, t), expected, t)
    assert_infinity(call, arguments, expected, rng)
    assert_layouts(call, arguments, expected)
    if name in SECTION:
        assert_dates_independent(call, arguments, expected, data.draw(st.permutations(range(len(expected)))))
    else:
        assert_columns(call, arguments, expected)
    assert all(identical(a, b) for a, b in zip(arguments, before, strict=True) if np.ndim(a)), "inputs changed"


@pytest.mark.parametrize("name", TIME_SERIES)
@EXAMPLES
@given(data=st.data())
def test_days_without_a_bar_are_skipped(name, data):
    call, dense = draw_case(data, name, dense=True)
    rng = data.draw(rngs())
    days = len(dense[0]) + data.draw(st.integers(0, 10))
    bars = np.zeros(days, dtype=bool)
    bars[rng.choice(days, len(dense[0]), replace=False)] = True
    out = call(*on_grids(lambda a: scatter(a, bars, rng), dense))
    assert identical(out[bars], call(*dense))
    assert np.isnan(out[~bars]).all()


@pytest.mark.parametrize("name", [name for name in WINDOWED if name not in ANCHORED])
@EXAMPLES
@given(data=st.data())
def test_windows_do_not_depend_on_where_the_grid_starts(name, data):
    op = REGISTRY[name]
    arguments, window = data.draw(cases(ROLES[op.kind], max(op.minimum, 1), min_rows=1))
    start = data.draw(st.integers(0, len(arguments[0]) - 1))
    full = op.call(*arguments, **window)[start:]
    later = op.call(*on_grids(lambda a: a[start:], arguments), **window)
    defined = ~np.isnan(later)
    assert identical(later[defined], full[defined])


def assert_prefix(call, arguments, expected, t):
    """Computing on the rows up to t gives rows up to t bit for bit."""
    assert identical(call(*on_grids(lambda a: a[: t + 1], arguments)), expected[: t + 1]), "prefix"


def assert_poison(call, poisoned, expected, t):
    """Other rows after t leave rows up to t unchanged."""
    assert identical(call(*poisoned)[: t + 1], expected[: t + 1]), "poison"


def assert_infinity(call, arguments, expected, rng):
    """±inf in place of NaN gives the same result."""
    infinite = on_grids(lambda a: np.where(np.isnan(a), rng.choice([np.inf, -np.inf], a.shape), a), arguments)
    assert identical(call(*infinite), expected), "infinity"


def assert_layouts(call, arguments, expected):
    """C, F, strided, read-only and broadcast inputs give the same result; F in gives F out, else C."""
    fortran, sparse = call(*on_grids(np.asfortranarray, arguments)), call(*on_grids(strided, arguments))
    assert identical(fortran, expected) and fortran.flags.f_contiguous, "Fortran order"
    assert identical(sparse, expected) and sparse.flags.c_contiguous and expected.flags.c_contiguous, "C order"
    assert identical(call(*on_grids(frozen, arguments)), expected), "read-only"
    first = on_grids(lambda a: a[:, :1], arguments)
    widened = call(*on_grids(lambda a: np.broadcast_to(a, (len(a), 3)), first))
    assert identical(widened, call(*on_grids(lambda a: np.repeat(a, 3, axis=1), first))), "broadcast"


def assert_columns(call, arguments, expected):
    """Each column computed as a 1-D series equals that column of the grid result."""
    for j in range(expected.shape[1]):
        column = call(*on_grids(lambda a, j=j: a[:, j], arguments))
        assert column.ndim == 1 and identical(column, expected[:, j]), f"column {j}"


def assert_dates_independent(call, arguments, expected, rows):
    """Each date's result uses that date's values only."""
    assert identical(call(*on_grids(lambda a: a[rows], arguments)), expected[rows]), "dates"


def poison(data, name, arguments, t):
    """Each grid argument with fresh rows, gaps included, after row t."""
    roles, (rows, cols) = ROLES[REGISTRY[name].kind], arguments[0].shape
    fresh = [data.draw(argument("c" if role == "c" else "x", rows - t - 1, cols)) for role in roles]
    return tuple(np.concatenate([a[: t + 1], b]) if np.ndim(a) else a for a, b in zip(arguments, fresh, strict=True))


def scatter(a, bars, rng):
    """a's rows on the bar days, NaN or ±inf on the others."""
    out = rng.choice([np.nan, np.inf, -np.inf], (len(bars), a.shape[1]))
    out[bars] = a
    return out


def strided(a):
    """a as a view of every other row and column of a larger C-order array."""
    parent = np.full((2 * a.shape[0], 2 * a.shape[1]), 7.0)
    parent[::2, ::2] = a
    return parent[::2, ::2]


def frozen(a):
    """A read-only copy, like the panel's arrays."""
    a = a.copy()
    a.flags.writeable = False
    return a


def valid_arguments(name, grid=GRID) -> list:
    """Fixed valid inputs for the operator, its window 3 last."""
    op = REGISTRY[name]
    return [grid % 2 if role == "c" else grid for role in ROLES[op.kind]] + ([3] if op.minimum is not None else [])


# Every series and cross-sectional operator reads its inputs through inputs.series(); one case per way in.
@pytest.mark.parametrize(
    ("name", "position"), [("MA", 0), ("CORR", 0), ("CORR", 1), ("COUNT", 0), ("VALUEWHEN", 1), ("CS_RANK", 0)]
)
def test_bool_and_int_series_raise_type_error(name, position):
    for dtype in (bool, np.int64):
        arguments = valid_arguments(name)
        arguments[position] = arguments[position].astype(dtype)
        with pytest.raises(TypeError):
            REGISTRY[name].call(*arguments)


@pytest.mark.parametrize("name", WINDOWED)
def test_window_arguments_are_checked(name):
    """A window is an integer (not a bool or a float) of at least the operator's own minimum."""
    op, data = REGISTRY[name], valid_arguments(name)[:-1]
    for n in (True, 3.0):
        with pytest.raises(TypeError):
            op.call(*data, n)
    with pytest.raises(ValueError):
        op.call(*data, op.minimum - 1)
    assert op.call(*data, op.minimum).shape == GRID.shape


def test_ref_cannot_look_ahead():
    with pytest.raises(ValueError):
        REF(GRID, -1)


@pytest.mark.parametrize(("n", "m", "error"), [(3, 4, ValueError), (3, 0, ValueError)])
def test_sma_weight_is_an_integer_from_1_to_n(n, m, error):
    with pytest.raises(error):
        SMA(GRID, n, m)


@pytest.mark.parametrize("name", ["CROSS", "ADD", "IF"])  # per_stock2, elementwise() and IF's own check
def test_series_and_grids_do_not_mix(name):
    # On a square grid numpy would broadcast a date series across the codes, reading later dates into earlier rows.
    for position in range(len(ROLES[REGISTRY[name].kind])):
        arguments = valid_arguments(name, SQUARE)
        arguments[position] = arguments[position][:, 0]
        with pytest.raises(ValueError):
            REGISTRY[name].call(*arguments)


@pytest.mark.parametrize("name", ["CS_RANK"])  # every cross-section goes through per_date
def test_cross_sections_need_a_grid(name):
    with pytest.raises(ValueError):
        REGISTRY[name].call(GRID[:, 0])


@pytest.mark.parametrize("name", ["ADD", "IF"])  # elementwise() and IF's own conversion
def test_elementwise_operators_accept_bool_int_and_scalars(name):
    call, arguments = REGISTRY[name].call, valid_arguments(name)
    for dtype in (bool, np.int64):
        typed = [a.astype(dtype) for a in arguments]
        np.testing.assert_array_equal(call(*typed), call(*(a.astype(float) for a in typed)))
        scalar = dtype(1)
        np.testing.assert_array_equal(call(scalar, *typed[1:]), call(np.full(GRID.shape, 1.0), *typed[1:]))


CACHE_PROBE = """
from numba.core.registry import CPUDispatcher
from wequant.ops import drivers, rolling, scan, section
for module in (drivers, rolling, scan, section):
    for name, f in vars(module).items():
        if isinstance(f, CPUDispatcher) and f.signatures:
            print(name, "cache" if f.stats.cache_hits and not f.stats.cache_misses else "compiled")
"""


def test_kernels_and_drivers_load_from_the_numba_cache():
    root = Path(__file__).parents[2]
    for _ in range(2):  # the first interpreter fills the cache when it is cold
        probe = subprocess.run(
            [sys.executable, "-c", CACHE_PROBE], cwd=root, capture_output=True, text=True, check=True
        )
    loaded = dict(line.split() for line in probe.stdout.splitlines())
    assert {"by_column", "by_column2", "by_row", "_mean", "_corr", "_rank"} <= loaded.keys()
    assert set(loaded.values()) == {"cache"}, loaded

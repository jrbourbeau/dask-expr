import operator
import pickle
import re

import dask
import numpy as np
import pandas as pd
import pytest
from dask.dataframe._compat import PANDAS_GT_210
from dask.dataframe.utils import assert_eq
from dask.utils import M

from dask_expr import expr, from_pandas, optimize
from dask_expr.datasets import timeseries
from dask_expr.reductions import Len


@pytest.fixture
def pdf():
    pdf = pd.DataFrame({"x": range(100)})
    pdf["y"] = pdf.x * 10.0
    yield pdf


@pytest.fixture
def df(pdf):
    yield from_pandas(pdf, npartitions=10)


def test_del(pdf, df):
    pdf = pdf.copy()

    # Check __delitem__
    del pdf["x"]
    del df["x"]
    assert_eq(pdf, df)


def test_setitem(pdf, df):
    pdf = pdf.copy()
    pdf["z"] = pdf.x + pdf.y

    df["z"] = df.x + df.y

    assert "z" in df.columns
    assert_eq(df, pdf)


def test_explode():
    pdf = pd.DataFrame({"a": [[1, 2], [3, 4]]})
    df = from_pandas(pdf)
    assert_eq(pdf.explode(column="a"), df.explode(column="a"))
    assert_eq(pdf.a.explode(), df.a.explode())


def test_meta_divisions_name():
    a = pd.DataFrame({"x": [1, 2, 3, 4], "y": [1.0, 2.0, 3.0, 4.0]})
    df = 2 * from_pandas(a, npartitions=2)
    assert list(df.columns) == list(a.columns)
    assert df.npartitions == 2

    assert np.isscalar(df.x.sum()._meta)
    assert df.x.sum().npartitions == 1

    assert "mul" in df._name
    assert "sum" in df.sum()._name


def test_meta_blockwise():
    a = pd.DataFrame({"x": [1, 2, 3, 4], "y": [1.0, 2.0, 3.0, 4.0]})
    b = pd.DataFrame({"z": [1, 2, 3, 4], "y": [1.0, 2.0, 3.0, 4.0]})

    aa = from_pandas(a, npartitions=2)
    bb = from_pandas(b, npartitions=2)

    cc = 2 * aa - 3 * bb
    assert set(cc.columns) == {"x", "y", "z"}


def test_dask(pdf, df):
    assert (df.x + df.y).npartitions == 10
    z = (df.x + df.y).sum()

    assert assert_eq(z, (pdf.x + pdf.y).sum())


@pytest.mark.parametrize(
    "func",
    [
        M.max,
        M.min,
        M.any,
        M.all,
        M.sum,
        M.prod,
        M.count,
        M.mean,
        M.idxmin,
        M.idxmax,
        pytest.param(
            lambda df: df.size,
            marks=pytest.mark.skip(reason="scalars don't work yet"),
        ),
    ],
)
def test_reductions(func, pdf, df):
    assert_eq(func(df), func(pdf))
    assert_eq(func(df.x), func(pdf.x))
    # check_dtype False because sub-selection of columns that is pushed through
    # is not reflected in the meta calculation
    assert_eq(func(df)["x"], func(pdf)["x"], check_dtype=False)


def test_nbytes(pdf, df):
    with pytest.raises(NotImplementedError, match="nbytes is not implemented"):
        df.nbytes
    assert_eq(df.x.nbytes, pdf.x.nbytes)


def test_mode():
    pdf = pd.DataFrame({"x": [1, 2, 3, 1, 2]})
    df = from_pandas(pdf, npartitions=3)

    assert_eq(df.x.mode(), pdf.x.mode(), check_names=False)


def test_value_counts(df, pdf):
    with pytest.raises(
        AttributeError, match="'DataFrame' object has no attribute 'value_counts'"
    ):
        df.value_counts()
    assert_eq(df.x.value_counts(), pdf.x.value_counts())


def test_dropna(pdf):
    pdf.loc[0, "y"] = np.nan
    df = from_pandas(pdf)
    assert_eq(df.dropna(), pdf.dropna())
    assert_eq(df.dropna(how="all"), pdf.dropna(how="all"))
    assert_eq(df.y.dropna(), pdf.y.dropna())


def test_memory_usage(pdf):
    # Results are not equal with RangeIndex because pandas has one RangeIndex while
    # we have one RangeIndex per partition
    pdf.index = np.arange(len(pdf))
    df = from_pandas(pdf)
    assert_eq(df.memory_usage(), pdf.memory_usage())
    assert_eq(df.memory_usage(index=False), pdf.memory_usage(index=False))
    assert_eq(df.x.memory_usage(), pdf.x.memory_usage())
    assert_eq(df.x.memory_usage(index=False), pdf.x.memory_usage(index=False))
    assert_eq(df.index.memory_usage(), pdf.index.memory_usage())
    with pytest.raises(TypeError, match="got an unexpected keyword"):
        df.index.memory_usage(index=True)


@pytest.mark.parametrize("func", [M.nlargest, M.nsmallest])
def test_nlargest_nsmallest(df, pdf, func):
    assert_eq(func(df, n=5, columns="x"), func(pdf, n=5, columns="x"))
    assert_eq(func(df.x, n=5), func(pdf.x, n=5))
    with pytest.raises(TypeError, match="got an unexpected keyword argument"):
        func(df.x, n=5, columns="foo")


@pytest.mark.parametrize(
    "func",
    [
        lambda df: df.x > 10,
        lambda df: df.x + 20 > df.y,
        lambda df: 10 < df.x,
        lambda df: 10 <= df.x,
        lambda df: 10 == df.x,
        lambda df: df.x < df.y,
        lambda df: df.x > df.y,
        lambda df: df.x == df.y,
        lambda df: df.x != df.y,
    ],
)
def test_conditionals(func, pdf, df):
    assert_eq(func(pdf), func(df), check_names=False)


@pytest.mark.parametrize(
    "func",
    [
        lambda df: df.x & df.y,
        lambda df: df.x.__rand__(df.y),
        lambda df: df.x | df.y,
        lambda df: df.x.__ror__(df.y),
        lambda df: df.x ^ df.y,
        lambda df: df.x.__rxor__(df.y),
    ],
)
def test_boolean_operators(func):
    pdf = pd.DataFrame(
        {"x": [True, False, True, False], "y": [True, False, False, False]}
    )
    df = from_pandas(pdf)
    assert_eq(func(pdf), func(df))


@pytest.mark.parametrize(
    "func",
    [
        lambda df: ~df,
        lambda df: ~df.x,
        lambda df: -df.z,
        lambda df: +df.z,
        lambda df: -df,
        lambda df: +df,
    ],
)
def test_unary_operators(func):
    pdf = pd.DataFrame(
        {"x": [True, False, True, False], "y": [True, False, False, False], "z": 1}
    )
    df = from_pandas(pdf)
    assert_eq(func(pdf), func(df))


@pytest.mark.parametrize(
    "func",
    [
        lambda df: df[(df.x > 10) | (df.x < 5)],
        lambda df: df[(df.x > 7) & (df.x < 10)],
    ],
)
def test_and_or(func, pdf, df):
    assert_eq(func(pdf), func(df), check_names=False)


@pytest.mark.parametrize("how", ["start", "end"])
def test_to_timestamp(pdf, how):
    pdf.index = pd.period_range("2019-12-31", freq="D", periods=len(pdf))
    df = from_pandas(pdf)
    assert_eq(df.to_timestamp(how=how), pdf.to_timestamp(how=how))
    assert_eq(df.x.to_timestamp(how=how), pdf.x.to_timestamp(how=how))


@pytest.mark.parametrize(
    "func",
    [
        lambda df: df.astype(int),
        lambda df: df.apply(lambda row, x, y=10: row * x + y, x=2),
        pytest.param(
            lambda df: df.map(lambda x: x + 1),
            marks=pytest.mark.skipif(
                not PANDAS_GT_210, reason="Only available from 2.1"
            ),
        ),
        lambda df: df.clip(lower=10, upper=50),
        lambda df: df.x.clip(lower=10, upper=50),
        lambda df: df.x.between(left=10, right=50),
        lambda df: df.x.map(lambda x: x + 1),
        lambda df: df.index.map(lambda x: x + 1),
        lambda df: df[df.x > 5],
        lambda df: df.assign(a=df.x + df.y, b=df.x - df.y),
        lambda df: df.replace(to_replace=1, value=1000),
        lambda df: df.x.replace(to_replace=1, value=1000),
        lambda df: df.isna(),
        lambda df: df.x.isna(),
        lambda df: df.abs(),
        lambda df: df.x.abs(),
        lambda df: df.rename(columns={"x": "xx"}),
        lambda df: df.rename(columns={"x": "xx"}).xx,
        lambda df: df.rename(columns={"x": "xx"})[["xx"]],
        lambda df: df.combine_first(df),
        lambda df: df.x.combine_first(df.y),
        lambda df: df.x.to_frame(),
        lambda df: df.x.index.to_frame(),
    ],
)
def test_blockwise(func, pdf, df):
    assert_eq(func(pdf), func(df))


def test_round(pdf):
    pdf += 0.5555
    df = from_pandas(pdf)
    assert_eq(df.round(decimals=1), pdf.round(decimals=1))
    assert_eq(df.x.round(decimals=1), pdf.x.round(decimals=1))


def test_repr(df):
    assert "+ 1" in str(df + 1)
    assert "+ 1" in repr(df + 1)

    s = (df["x"] + 1).sum(skipna=False).expr
    assert '["x"]' in s or "['x']" in s
    assert "+ 1" in s
    assert "sum(skipna=False)" in s


def test_rename_traverse_filter(df):
    result = optimize(df.rename(columns={"x": "xx"})[["xx"]], fuse=False)
    expected = df[["x"]].rename(columns={"x": "xx"})
    assert str(result) == str(expected)


def test_columns_traverse_filters(pdf, df):
    result = optimize(df[df.x > 5].y, fuse=False)
    expected = df.y[df.x > 5]

    assert str(result) == str(expected)


def test_clip_traverse_filters(df):
    result = optimize(df.clip(lower=10).y, fuse=False)
    expected = df.y.clip(lower=10)

    assert result._name == expected._name

    result = optimize(df.clip(lower=10)[["x", "y"]], fuse=False)
    expected = df.clip(lower=10)

    assert result._name == expected._name


@pytest.mark.parametrize("projection", ["zz", ["zz"], ["zz", "x"], "zz"])
@pytest.mark.parametrize("subset", ["x", ["x"]])
def test_drop_duplicates_subset_optimizing(pdf, subset, projection):
    pdf["zz"] = 1
    df = from_pandas(pdf)
    result = optimize(df.drop_duplicates(subset=subset)[projection], fuse=False)
    expected = df[["x", "zz"]].drop_duplicates(subset=subset)[projection]

    assert str(result) == str(expected)


def test_broadcast(pdf, df):
    assert_eq(
        df + df.sum(),
        pdf + pdf.sum(),
    )
    assert_eq(
        df.x + df.x.sum(),
        pdf.x + pdf.x.sum(),
    )


def test_persist(pdf, df):
    a = df + 2
    b = a.persist()

    assert_eq(a, b)
    assert len(a.__dask_graph__()) > len(b.__dask_graph__())

    assert len(b.__dask_graph__()) == b.npartitions

    assert_eq(b.y.sum(), (pdf + 2).y.sum())


def test_index(pdf, df):
    assert_eq(df.index, pdf.index)
    assert_eq(df.x.index, pdf.x.index)


@pytest.mark.parametrize("drop", [True, False])
def test_reset_index(pdf, df, drop):
    assert_eq(df.reset_index(drop=drop), pdf.reset_index(drop=drop), check_index=False)
    assert_eq(
        df.x.reset_index(drop=drop), pdf.x.reset_index(drop=drop), check_index=False
    )


def test_head(pdf, df):
    assert_eq(df.head(compute=False), pdf.head())
    assert_eq(df.head(compute=False, n=7), pdf.head(n=7))

    assert df.head(compute=False).npartitions == 1


def test_head_down(df):
    result = (df.x + df.y + 1).head(compute=False)
    optimized = optimize(result)

    assert_eq(result, optimized)

    assert not isinstance(optimized.expr, expr.Head)


def test_head_head(df):
    a = df.head(compute=False).head(compute=False)
    b = df.head(compute=False)

    assert a.optimize()._name == b.optimize()._name


def test_tail(pdf, df):
    assert_eq(df.tail(compute=False), pdf.tail())
    assert_eq(df.tail(compute=False, n=7), pdf.tail(n=7))

    assert df.tail(compute=False).npartitions == 1


def test_tail_down(df):
    result = (df.x + df.y + 1).tail(compute=False)
    optimized = optimize(result)

    assert_eq(result, optimized)

    assert not isinstance(optimized.expr, expr.Tail)


def test_tail_tail(df):
    a = df.tail(compute=False).tail(compute=False)
    b = df.tail(compute=False)

    assert a.optimize()._name == b.optimize()._name


def test_projection_stacking(df):
    result = df[["x", "y"]]["x"]
    optimized = optimize(result, fuse=False)
    expected = df["x"]

    assert optimized._name == expected._name


def test_projection_stacking_coercion(pdf):
    df = from_pandas(pdf)
    assert_eq(df.x[0], pdf.x[0], check_divisions=False)
    assert_eq(df.x[[0]], pdf.x[[0]], check_divisions=False)


def test_remove_unnecessary_projections(df):
    result = (df + 1)[df.columns]
    optimized = optimize(result, fuse=False)
    expected = df + 1

    assert optimized._name == expected._name

    result = (df.x + 1)["x"]
    optimized = optimize(result, fuse=False)
    expected = df.x + 1

    assert optimized._name == expected._name


def test_substitute(df):
    pdf = pd.DataFrame(
        {
            "a": range(100),
            "b": range(100),
            "c": range(100),
        }
    )
    df = from_pandas(pdf, npartitions=3)
    df = df.expr

    result = (df + 1).substitute({1: 2})
    expected = df + 2
    assert result._name == expected._name

    result = df["a"].substitute({df["a"]: df["b"]})
    expected = df["b"]
    assert result._name == expected._name

    result = (df["a"] - df["b"]).substitute({df["b"]: df["c"]})
    expected = df["a"] - df["c"]
    assert result._name == expected._name

    result = df["a"].substitute({3: 4})
    expected = from_pandas(pdf, npartitions=4).a
    assert result._name == expected._name

    result = (df["a"].sum() + 5).substitute({df["a"]: df["b"], 5: 6})
    expected = df["b"].sum() + 6
    assert result._name == expected._name


def test_from_pandas(pdf):
    df = from_pandas(pdf, npartitions=3)
    assert df.npartitions == 3
    assert "pandas" in df._name


def test_copy(pdf, df):
    original = df.copy()
    columns = tuple(original.columns)

    df["z"] = df.x + df.y

    assert tuple(original.columns) == columns
    assert "z" not in original.columns


def test_partitions(pdf, df):
    assert_eq(df.partitions[0], pdf.iloc[:10])
    assert_eq(df.partitions[1], pdf.iloc[10:20])
    assert_eq(df.partitions[1:3], pdf.iloc[10:30])
    assert_eq(df.partitions[[3, 4]], pdf.iloc[30:50])
    assert_eq(df.partitions[-1], pdf.iloc[90:])

    out = (df + 1).partitions[0].optimize(fuse=False)
    assert isinstance(out.expr, expr.Add)
    assert out.expr.left._partitions == [0]

    # Check culling
    out = optimize(df.partitions[1])
    assert len(out.dask) == 1
    assert_eq(out, pdf.iloc[10:20])


def test_column_getattr(df):
    df = df.expr
    assert df.x._name == df["x"]._name

    with pytest.raises(AttributeError):
        df.foo


def test_serialization(pdf, df):
    before = pickle.dumps(df)

    assert len(before) < 200 + len(pickle.dumps(pdf))

    part = df.partitions[0].compute()
    assert (
        len(pickle.dumps(df.__dask_graph__()))
        < 1000 + len(pickle.dumps(part)) * df.npartitions
    )

    after = pickle.dumps(df)

    assert before == after  # caching doesn't affect serialization

    assert pickle.loads(before)._name == pickle.loads(after)._name
    assert_eq(pickle.loads(before), pickle.loads(after))


def test_size_optimized(df):
    expr = (df.x + 1).apply(lambda x: x).size
    out = optimize(expr)
    expected = optimize(df.x.size)
    assert out._name == expected._name

    expr = (df + 1).apply(lambda x: x).size
    out = optimize(expr)
    expected = optimize(df.size)
    assert out._name == expected._name


@pytest.mark.parametrize("fuse", [True, False])
def test_tree_repr(df, fuse):
    df = timeseries()
    expr = ((df.x + 1).sum(skipna=False) + df.y.mean()).expr
    expr = expr.optimize() if fuse else expr
    s = expr.tree_repr()

    assert "Sum" in s
    assert "Add" in s
    assert "1" in s
    assert "True" not in s
    assert "None" not in s
    assert "skipna=False" in s
    assert str(df.seed) in s.lower()
    if fuse:
        assert "Fused" in s
        assert s.count("|") == 9


def test_simple_graphs(df):
    expr = (df + 1).expr
    graph = expr.__dask_graph__()

    assert graph[(expr._name, 0)] == (operator.add, (df.expr._name, 0), 1)


def test_map_partitions(df):
    def combine_x_y(x, y, foo=None):
        assert foo == "bar"
        return x + y

    df2 = df.map_partitions(combine_x_y, df + 1, foo="bar")
    assert_eq(df2, df + (df + 1))


def test_map_partitions_broadcast(df):
    def combine_x_y(x, y, val, foo=None):
        assert foo == "bar"
        return x + y + val

    df2 = df.map_partitions(combine_x_y, df["x"].sum(), 123, foo="bar")
    assert_eq(df2, df + df["x"].sum() + 123)
    assert_eq(df2.optimize(), df + df["x"].sum() + 123)


@pytest.mark.parametrize("opt", [True, False])
def test_map_partitions_merge(opt):
    # Make simple left & right dfs
    pdf1 = pd.DataFrame({"x": range(20), "y": range(20)})
    df1 = from_pandas(pdf1, 2)
    pdf2 = pd.DataFrame({"x": range(0, 20, 2), "z": range(10)})
    df2 = from_pandas(pdf2, 1)

    # Partition-wise merge with map_partitions
    df3 = df1.map_partitions(
        lambda l, r: l.merge(r, on="x"),
        df2,
        enforce_metadata=False,
        clear_divisions=True,
    )

    # Check result with/without fusion
    expect = pdf1.merge(pdf2, on="x")
    df3 = (df3.optimize() if opt else df3)[list(expect.columns)]
    assert_eq(df3, expect, check_index=False)


def test_depth(df):
    assert df._depth() == 1
    assert (df + 1)._depth() == 2
    assert ((df.x + 1) + df.y)._depth() == 4


def test_partitions_nested(df):
    a = expr.Partitions(expr.Partitions(df.expr, [2, 4, 6]), [0, 2])
    b = expr.Partitions(df.expr, [2, 6])

    assert a.optimize()._name == b.optimize()._name


@pytest.mark.parametrize("sort", [True, False])
@pytest.mark.parametrize("npartitions", [7, 12])
def test_repartition_npartitions(pdf, npartitions, sort):
    df = from_pandas(pdf, sort=sort) + 1
    df2 = df.repartition(npartitions=npartitions)
    assert df2.npartitions == npartitions
    assert_eq(df, df2)


@pytest.mark.parametrize("opt", [True, False])
def test_repartition_divisions(df, opt):
    end = df.divisions[-1] + 100
    stride = end // (df.npartitions + 2)
    divisions = tuple(range(0, end, stride))
    df2 = (df + 1).repartition(divisions=divisions, force=True)["x"]
    df2 = optimize(df2) if opt else df2
    assert df2.divisions == divisions
    assert_eq((df + 1)["x"], df2)

    # Check partitions
    for p, part in enumerate(dask.compute(list(df2.index.partitions))[0]):
        if len(part):
            assert part.min() >= df2.divisions[p]
            assert part.max() < df2.divisions[p + 1]


def test_len(df, pdf):
    df2 = df[["x"]] + 1
    assert len(df2) == len(pdf)

    assert len(df[df.x > 5]) == len(pdf[pdf.x > 5])

    first = df2.partitions[0].compute()
    assert len(df2.partitions[0]) == len(first)

    assert isinstance(Len(df2.expr).optimize(), expr.Literal)
    assert isinstance(expr.Lengths(df2.expr).optimize(), expr.Literal)


def test_drop_duplicates(df, pdf):
    assert_eq(df.drop_duplicates(), pdf.drop_duplicates())
    assert_eq(
        df.drop_duplicates(ignore_index=True), pdf.drop_duplicates(ignore_index=True)
    )
    assert_eq(df.drop_duplicates(subset=["x"]), pdf.drop_duplicates(subset=["x"]))
    assert_eq(df.x.drop_duplicates(), pdf.x.drop_duplicates())

    with pytest.raises(KeyError, match=re.escape("Index(['a'], dtype='object')")):
        df.drop_duplicates(subset=["a"])

    with pytest.raises(TypeError, match="got an unexpected keyword argument"):
        df.x.drop_duplicates(subset=["a"])


def test_unique(df, pdf):
    with pytest.raises(
        AttributeError, match="'DataFrame' object has no attribute 'unique'"
    ):
        df.unique()

    # pandas returns a numpy array while we return a Series/Index
    assert_eq(df.x.unique(), pd.Series(pdf.x.unique(), name="x"))
    assert_eq(df.index.unique(), pd.Index(pdf.index.unique()))


def test_find_operations(df):
    df2 = df[df["x"] > 1][["y"]] + 1

    filters = list(df2.find_operations(expr.Filter))
    assert len(filters) == 1

    projections = list(df2.find_operations(expr.Projection))
    assert len(projections) == 2

    adds = list(df2.find_operations(expr.Add))
    assert len(adds) == 1
    assert next(iter(adds))._name == df2._name


def test_dir(df):
    assert all(c in dir(df) for c in df.columns)
    assert "sum" in dir(df)
    assert "sum" in dir(df.x)
    assert "sum" in dir(df.index)


def test_sample(df):
    result = df.sample(frac=0.5)

    assert_eq(result, result)

    result = df.sample(frac=0.5, random_state=1234)
    expected = df.sample(frac=0.5, random_state=1234)
    assert_eq(result, expected)

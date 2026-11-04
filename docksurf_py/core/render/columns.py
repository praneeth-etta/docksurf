"""Column subsets: the compact layout shows fewer columns than the wide one.
Populate code builds the full cell tuple once; this picks the visible cells."""

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def select_cells(
    columns: Sequence[str], all_columns: Sequence[str], cells: Sequence[T]
) -> tuple[T, ...]:
    """The cells for `columns`, in `columns` order, from a full row `cells` laid
    out as `all_columns`. A column name not in `all_columns`, or a row whose
    length doesn't match it, is a programming error and raises."""
    if len(cells) != len(all_columns):
        raise ValueError(
            f"{len(cells)} cells for {len(all_columns)} columns {tuple(all_columns)}"
        )
    index = {name: i for i, name in enumerate(all_columns)}
    return tuple(cells[index[name]] for name in columns)

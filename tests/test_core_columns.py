"""Unit tests for select_cells — the compact column subset picker."""

import unittest

from docksurf_py.core.render.columns import select_cells

ALL = ("Name", "State", "Uptime", "Ports", "Image")
CELLS = ("n", "s", "u", "p", "i")


class SelectCellsTests(unittest.TestCase):
    def test_full_set_is_identity(self) -> None:
        self.assertEqual(select_cells(ALL, ALL, CELLS), CELLS)

    def test_subset_keeps_the_requested_order(self) -> None:
        self.assertEqual(select_cells(("Name", "Uptime"), ALL, CELLS), ("n", "u"))
        self.assertEqual(select_cells(("Image", "Name"), ALL, CELLS), ("i", "n"))

    def test_empty_selection(self) -> None:
        self.assertEqual(select_cells((), ALL, CELLS), ())

    def test_unknown_column_raises(self) -> None:
        with self.assertRaises(KeyError):
            select_cells(("Nope",), ALL, CELLS)

    def test_row_length_mismatch_raises(self) -> None:
        with self.assertRaises(ValueError):
            select_cells(("Name",), ALL, CELLS[:4])


if __name__ == "__main__":
    unittest.main()

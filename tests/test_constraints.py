"""Check inventory propagation against small independent assignments."""

from collections import Counter
from itertools import product
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from block_laser import Puzzle
from constraints import (
    ALL, EMPTY, KIND_MASKS, OPAQUE, REFLECT, SPLIT, DomainStore,
)


def make_board(cells, inventory=(0, 0, 0)):
    """Build an inventory-only fixture; light paths are irrelevant here."""
    return Puzzle(
        (tuple(cells),), dict(zip("ABC", inventory)), (), frozenset(),
    )


def exact_assignments(size, inventory):
    """Enumerate inventory-correct assignments without propagation code."""
    quotas = (size - sum(inventory),) + inventory
    return [
        cells for cells in product("oABC", repeat=size)
        if tuple(cells.count(kind) for kind in "oABC") == quotas
    ]


class ConstraintTests(unittest.TestCase):
    def test_no_movable_cells_preserve_fixed_layout(self):
        board = make_board("xABC")
        store = DomainStore(board)
        self.assertEqual(store.movable, ())
        for strong in (False, True):
            with self.subTest(strong=strong):
                self.assertIs(
                    store.propagate(store.initial, strong), store.initial,
                )
                self.assertEqual(store.complete(store.initial), board.cells)

    def test_one_movable_cell_uses_its_inventory(self):
        for kind in "oABC":
            with self.subTest(kind=kind):
                inventory = tuple(int(kind == name) for name in "ABC")
                board = make_board("xAoCB", inventory)
                store = DomainStore(board)
                domains = (ALL,) * len(board.cells)
                narrowed = store.propagate(domains, strong=True)
                self.assertEqual(narrowed[2], KIND_MASKS[kind])
                expected = list(board.cells)
                expected[2] = kind
                self.assertEqual(store.complete(narrowed), tuple(expected))
                incompatible = list(domains)
                incompatible[2] = ALL & ~KIND_MASKS[kind]
                self.assertIsNone(store.propagate(incompatible, strong=True))

    def test_fixed_and_unavailable_cells_do_not_consume_inventory(self):
        board = make_board("oAxoBCo", (1, 0, 1))
        store = DomainStore(board)
        self.assertEqual(store.movable, (0, 3, 6))
        domains = list(store.initial)
        domains[0] = REFLECT
        domains[3] = EMPTY | REFLECT
        domains[6] = EMPTY | SPLIT
        narrowed = store.propagate(domains)
        self.assertEqual(
            narrowed,
            (REFLECT, REFLECT, EMPTY, EMPTY, OPAQUE, SPLIT, SPLIT),
        )
        self.assertEqual(
            store.complete(narrowed), ("A", "A", "x", "o", "B", "C", "C"),
        )

    def test_branch_restrictions_leave_parent_domains_unchanged(self):
        store = DomainStore(make_board("oooo", (1, 1, 1)))
        parent = store.initial
        self.assertIs(store.propagate(parent, strong=True), parent)
        child = store.restrict(parent, 0, REFLECT | SPLIT, strong=True)
        self.assertIsNotNone(child)
        self.assertIsNot(child, parent)
        self.assertEqual(parent, (ALL,) * 4)
        saved_child = tuple(child)
        self.assertIsNone(store.restrict(child, 0, OPAQUE, strong=True))
        self.assertEqual(child, saved_child)
        self.assertIs(store.propagate(child, strong=True), child)
        mutable_parent = list(parent)
        self.assertEqual(store.propagate(mutable_parent, strong=True), parent)
        self.assertEqual(mutable_parent, list(parent))

    def test_invalid_domains_and_individual_inventory_conflicts(self):
        store = DomainStore(make_board("oooo", (1, 1, 1)))
        with self.assertRaisesRegex(ValueError, "Domain count"):
            store.propagate((ALL,))
        self.assertIsNone(store.propagate((0, ALL, ALL, ALL)))
        self.assertIsNone(store.propagate((REFLECT, REFLECT, ALL, ALL)))
        self.assertIsNone(store.propagate((ALL & ~SPLIT,) * 4))
        fixed = DomainStore(make_board("Ao", (1, 0, 0)))
        self.assertIsNone(fixed.propagate((EMPTY, ALL)))

    def test_hall_subset_detects_competing_cells(self):
        store = DomainStore(make_board("oooooo", (1, 2, 2)))
        domains = (EMPTY | REFLECT,) * 3 + (OPAQUE | SPLIT,) * 3
        # Individual types have enough candidate cells, but three cells
        # compete for the two total empty/A positions.
        self.assertIs(store.propagate(domains), domains)
        self.assertIsNone(store.propagate(domains, strong=True))
        self.assertIsNone(store.complete(domains))

    def test_hall_subset_forces_the_remaining_types(self):
        inventory = (1, 2, 2)
        store = DomainStore(make_board("oooooo", inventory))
        domains = (EMPTY | REFLECT,) * 2 + (ALL,) * 4
        self.assertIs(store.propagate(domains), domains)
        narrowed = store.propagate(domains, strong=True)
        self.assertEqual(
            narrowed, (EMPTY | REFLECT,) * 2 + (OPAQUE | SPLIT,) * 4,
        )
        completed = store.complete(narrowed)
        self.assertIsNotNone(completed)
        self.assertEqual(
            tuple(Counter(completed)[kind] for kind in "oABC"), (1, 1, 2, 2),
        )

    def test_propagation_preserves_all_small_inventory_assignments(self):
        randomizer = random.Random(20261111)
        for size, inventory in ((4, (1, 1, 1)), (5, (2, 1, 1))):
            store = DomainStore(make_board("o" * size, inventory))
            layouts = exact_assignments(size, inventory)
            queries = [
                tuple(randomizer.randrange(16) for _ in range(size))
                for _ in range(100)
            ]
            # Include guaranteed-feasible queries as well as arbitrary masks.
            for _ in range(30):
                witness = randomizer.choice(layouts)
                queries.append(tuple(
                    KIND_MASKS[kind] | randomizer.randrange(16)
                    for kind in witness
                ))
            for domains in queries:
                feasible = [
                    cells for cells in layouts
                    if all(KIND_MASKS[kind] & mask
                           for kind, mask in zip(cells, domains))
                ]
                for strong in (False, True):
                    with self.subTest(size=size, domains=domains, strong=strong):
                        propagated = store.propagate(domains, strong)
                        if propagated is None:
                            self.assertFalse(feasible)
                            continue
                        for cells in feasible:
                            self.assertTrue(all(
                                KIND_MASKS[kind] & mask
                                for kind, mask in zip(cells, propagated)
                            ))
                        completed = store.complete(propagated)
                        if feasible:
                            self.assertIn(completed, feasible)
                        else:
                            self.assertIsNone(completed)


if __name__ == "__main__":
    unittest.main()

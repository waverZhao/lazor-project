"""Shared cell domains and exact movable-inventory constraints.

A path does not have to choose a concrete block immediately: straight travel
permits empty/C, reflection permits A/C, and requiring both leaves only C.
All paths intersect the same cell domains, so their optical requirements stay
globally consistent.
"""

from collections import Counter
from operator import and_, itemgetter
from typing import Optional, Sequence, Tuple

from block_laser import (
    BLOCK_BEHAVIORS, REFLECTION_FLAG, TRANSMISSION_FLAG, Puzzle as Board,
)


EMPTY = 1
REFLECT = 2
OPAQUE = 4
SPLIT = 8
ALL = EMPTY | REFLECT | OPAQUE | SPLIT
BITS = (EMPTY, REFLECT, OPAQUE, SPLIT)
SYMBOLS = ("o", "A", "B", "C")
KIND_MASKS = dict(zip(SYMBOLS, BITS))
# These are sets of possible concrete types, not the two optical switch bits.
# Deriving them from the same truth table prevents the two layers drifting.
STRAIGHT = sum(bit for kind, bit in KIND_MASKS.items()
               if BLOCK_BEHAVIORS[kind] & TRANSMISSION_FLAG)
REFLECTION = sum(bit for kind, bit in KIND_MASKS.items()
                 if BLOCK_BEHAVIORS[kind] & REFLECTION_FLAG)


class DomainStore:
    """Cell domains with exact empty/A/B/C counts on original movable cells.

    Fixed blocks and unavailable cells participate in optical restrictions,
    but their contents never consume movable inventory. ``propagate`` and
    ``restrict`` return immutable tuples, leaving their input untouched.
    """

    def __init__(self, board: Board):
        self.board = board
        self.movable = tuple(
            index for index, kind in enumerate(board.cells) if kind == "o"
        )
        self._gather = (
            itemgetter(*self.movable) if len(self.movable) > 1 else None
        )
        empty_count = len(self.movable) - sum(board.inventory)
        if empty_count < 0 or any(count < 0 for count in board.inventory):
            raise ValueError("Movable inventory does not fit the available cells")
        self.quotas = (empty_count,) + tuple(board.inventory)
        available = sum(bit for bit, quota in zip(BITS, self.quotas) if quota)
        self.initial = tuple(
            available if kind == "o" else EMPTY if kind == "x" else KIND_MASKS[kind]
            for kind in board.cells
        )
        self.propagations = 0
        self._subsets = tuple(
            (mask, sum(quota for bit, quota in zip(BITS, self.quotas) if bit & mask))
            for mask in range(1, ALL)
            if mask not in BITS
        )

    def propagate(
        self, domains: Sequence[int], strong: bool = False
    ) -> Optional[Tuple[int, ...]]:
        """Enforce inventory bounds until stable, optionally with Hall subsets.

        A type must appear at least as often as its singleton domains and at
        most as often as the domains containing it. Tight bounds force or
        remove values. Strong mode applies the same rule to subsets of types,
        detecting competition that individual type counts cannot see.

        Count movable domains once per round. Scan cells again only when a
        tight bound forces an update, then rebuild the histogram before using
        any more bounds.
        """
        self.propagations += 1
        if len(domains) != len(self.initial):
            raise ValueError("Domain count does not match the board")
        original = domains if isinstance(domains, tuple) else tuple(domains)
        values = list(map(and_, original, self.initial))
        if 0 in values:
            return None
        movable = self.movable
        while True:
            histogram = Counter(self._movable_values(values))
            changed = False
            for bit, quota in zip(BITS, self.quotas):
                required = histogram.get(bit, 0)
                possible = sum(
                    count for mask, count in histogram.items() if mask & bit
                )
                if required > quota or possible < quota:
                    return None
                if possible == required:
                    continue
                if required == quota:
                    for index in movable:
                        value = values[index]
                        if value != bit and value & bit:
                            values[index] = value & ~bit
                            changed = True
                elif possible == quota:
                    for index in movable:
                        if values[index] & bit:
                            values[index] = bit
                    changed = True
                if changed:
                    break
            if not changed and strong:
                for subset, quota in self._subsets:
                    required = sum(
                        count for mask, count in histogram.items()
                        if mask & ~subset == 0
                    )
                    possible = sum(
                        count for mask, count in histogram.items()
                        if mask & subset
                    )
                    if required > quota or possible < quota:
                        return None
                    if required == quota:
                        for index in movable:
                            value = values[index]
                            if value & subset and value & ~subset:
                                values[index] = value & ~subset
                                changed = True
                    elif possible == quota:
                        for index in movable:
                            value = values[index]
                            if value & subset and value & ~subset:
                                values[index] = value & subset
                                changed = True
                    if changed:
                        break
            if not changed:
                break
        result = tuple(values)
        return original if result == original else result

    def _movable_values(self, values):
        """Gather movable masks, including zero- and one-cell boards."""
        if len(self.movable) > 1:
            return self._gather(values)
        return [values[index] for index in self.movable]

    def restrict(
        self,
        domains: Sequence[int],
        cell: int,
        allowed: int,
        strong: bool = False,
    ) -> Optional[Tuple[int, ...]]:
        """Intersect one cell's requirements and propagate shared inventory."""
        reduced = domains[cell] & allowed & self.initial[cell]
        if not reduced:
            return None
        if reduced == domains[cell]:
            return domains if isinstance(domains, tuple) else tuple(domains)
        values = list(domains)
        values[cell] = reduced
        return self.propagate(values, strong=strong)

    def complete(self, domains: Sequence[int]) -> Optional[Tuple[str, ...]]:
        """Find an exact-inventory completion by capacitated bipartite matching.

        There are only four type nodes. Augmenting paths move an earlier cell
        to another allowed type when the preferred type is already full.
        This avoids enumerating arrangements of optically irrelevant cells.
        """
        values = self.propagate(domains, strong=True)
        if values is None:
            return None
        assigned = {}
        buckets = [[] for _ in BITS]

        def place(cell, seen_types):
            for type_index, bit in enumerate(BITS):
                if not values[cell] & bit or type_index in seen_types:
                    continue
                seen_types.add(type_index)
                if len(buckets[type_index]) < self.quotas[type_index]:
                    buckets[type_index].append(cell)
                    assigned[cell] = type_index
                    return True
                for previous in tuple(buckets[type_index]):
                    if place(previous, seen_types):
                        buckets[type_index].remove(previous)
                        buckets[type_index].append(cell)
                        assigned[cell] = type_index
                        return True
            return False

        ordered = sorted(self.movable, key=lambda cell: (bin(values[cell]).count("1"), cell))
        for cell in ordered:
            if not place(cell, set()):
                return None
        result = list(self.board.cells)
        for cell in self.movable:
            result[cell] = SYMBOLS[assigned[cell]]
        return tuple(result)

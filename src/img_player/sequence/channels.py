"""Group raw EXR channel names into layer-level entries.

Multichannel EXRs typically expose channels named like a flat list:

    R, G, B, A, volume_Z,
    albedo.R, albedo.G, albedo.B,
    diffuse.R, diffuse.G, diffuse.B,
    normal.X, normal.Y, normal.Z,
    crypto00.r, crypto00.g, crypto00.b, crypto00.a,
    Z

Showing each one as a separate entry in the channel selector floods
the user with a hundred options and breaks the natural workflow —
artists think in *layers* (= passes), not in individual channels.
This module collapses the flat list into a UI-friendly representation
where ``albedo.R``/``.G``/``.B`` becomes a single ``"albedo"`` entry
that loads the three channels as an RGB composite.

The output is ordered to match the user's mental model:
1. The default ``"RGB"`` (or ``"RGBA"``) entry first — the beauty pass.
2. Other RGB-shaped layers in their original order (``albedo``,
   ``diffuse``, …).
3. Anything that wasn't grouped (``Z``, ``volume_Z``, single-channel
   masks, normals if not grouped) — listed last as ``layer.sub``
   so the user can still reach them.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

# RGB-like sub-channel names recognised when grouping. Some renderers
# write lower-case (Cryptomatte does), so we normalise.
_RGB_SUBS = ("r", "g", "b")
_ALPHA_SUBS = ("a",)


@dataclass(frozen=True)
class ChannelGroup:
    """One entry in the UI selector.

    ``label`` is what we show in the combo box. ``channels`` is the
    raw OIIO channel list passed to ``read_frame`` (preserves OIIO
    naming case).
    """

    label: str
    channels: tuple[str, ...]


def _split_layer(name: str) -> tuple[str, str | None]:
    """Split ``"albedo.R"`` → ``("albedo", "R")``; ``"Z"`` → ``("Z", None)``.

    EXR uses ``.`` as the layer separator. Names without one are
    bare (R, G, B, A at the root, or single-channel masks).
    """
    if "." not in name:
        return name, None
    head, _, tail = name.rpartition(".")
    return head, tail


def group_channels(raw: Iterable[str]) -> list[ChannelGroup]:
    """Convert a flat list of EXR channel names into UI groups.

    Rules:

    * The bare ``R``/``G``/``B`` (+ optional ``A``) at the root
      become a single ``"RGB"`` (or ``"RGBA"``) entry — the beauty
      pass, always first.
    * Any layer that has at least R, G and B sub-channels becomes a
      single layer entry (e.g. ``"albedo"``) loading the three (or
      four with alpha) channels as a composite.
    * Everything else stays individual: ``"Z"``, ``"volume_Z"``,
      ``"normal.X"`` (if normal didn't have R/G/B), single masks…

    The list preserves the original ordering of the input so that
    layer order from the renderer is respected (diffuse before
    specular, etc.).
    """
    raw_list = list(raw)

    # Per-layer accumulator of {sub_lower: original_name}. We index
    # by lowercase for matching but keep the original case in the
    # output channels.
    by_layer: dict[str, dict[str, str]] = {}
    # First-seen index for each layer — drives the output order.
    layer_order: list[str] = []
    # Channels with no "." (bare names like "R", "Z").
    bare_channels: list[str] = []

    for ch in raw_list:
        layer, sub = _split_layer(ch)
        if sub is None:
            bare_channels.append(ch)
            continue
        if layer not in by_layer:
            by_layer[layer] = {}
            layer_order.append(layer)
        by_layer[layer][sub.lower()] = ch

    groups: list[ChannelGroup] = []

    # 1. Root RGB(A) — the beauty pass.
    bare_lower = {b.lower(): b for b in bare_channels}
    if all(s in bare_lower for s in _RGB_SUBS):
        chans = tuple(bare_lower[s] for s in _RGB_SUBS)
        if "a" in bare_lower:
            chans = chans + (bare_lower["a"],)
            groups.append(ChannelGroup("RGBA", chans))
        else:
            groups.append(ChannelGroup("RGB", chans))
        # Mark these as "consumed" so they don't reappear later.
        consumed = set(chans)
        bare_channels = [b for b in bare_channels if b not in consumed]

    # 2. RGB-shaped layers (albedo, diffuse, specular…).
    for layer in layer_order:
        subs = by_layer[layer]
        if all(s in subs for s in _RGB_SUBS):
            chans = tuple(subs[s] for s in _RGB_SUBS)
            if "a" in subs:
                chans = chans + (subs["a"],)
            groups.append(ChannelGroup(layer, chans))
            # Remove the consumed sub-channels so they don't
            # re-appear individually below.
            for s in _RGB_SUBS + _ALPHA_SUBS:
                subs.pop(s, None)

    # 3. Leftover sub-channels (e.g. ``normal.X`` if normal had no
    # R/G/B, or AOVs that only have one component). Listed in the
    # original order.
    for layer in layer_order:
        subs = by_layer[layer]
        for sub_name, original in subs.items():
            groups.append(ChannelGroup(original, (original,)))

    # 4. Bare leftovers (Z, masks…). Often the most useful ones for
    # inspection, but listed last because they're the "secondary"
    # channels.
    for ch in bare_channels:
        groups.append(ChannelGroup(ch, (ch,)))

    return groups


# ---------------------------------------------------------------- Selection model
#
# A ``ChannelSelection`` describes WHAT the user wants to see in the
# viewport at any given moment. It carries two pieces of state:
#
# * ``active`` — the single "current" group (= what was clickable in the
#   old combo box). Always non-None once a sequence is loaded.
# * ``tiles`` — zero or more groups checked for the contact-sheet view.
#   When empty, the viewport shows ``active`` only (legacy mode). When
#   non-empty, the viewport shows the tiles in a grid and the active
#   group is purely a "next single channel to fall back to" reference.
#
# The same dataclass is the single source of truth used by:
#   - the cache (decode the union of all displayed channels in one OIIO call),
#   - the contact-sheet compositor (split the union back into per-tile arrays),
#   - the UI menu (round-trip to QSettings, render the popup state).
#
# Frozen + slots-friendly: cheap to compare in signal slots so the
# controller can early-return when nothing actually changed.


@dataclass(frozen=True)
class ChannelSelection:
    """Channels to display: one ``active`` + zero or more contact-sheet ``tiles``.

    Empty ``tiles`` → single mode (display ``active`` as before).
    Non-empty ``tiles`` → contact-sheet mode (display each tile in a grid,
    in the order given).
    """

    active: ChannelGroup
    tiles: tuple[ChannelGroup, ...] = field(default_factory=tuple)

    @property
    def is_contact_sheet(self) -> bool:
        return len(self.tiles) > 0

    @property
    def displayed(self) -> tuple[ChannelGroup, ...]:
        """Groups actually painted by the viewport."""
        return self.tiles if self.is_contact_sheet else (self.active,)

    def union_channels(self) -> tuple[str, ...]:
        """Deduplicated, order-preserving union of all displayed channels.

        This is what we pass to OIIO ``read_image`` so a single decode
        pass loads everything the viewport needs — re-reading the same
        EXR once per tile would be wasteful (the OS cache helps but
        OIIO setup costs aren't free).
        """
        seen: dict[str, None] = {}
        for group in self.displayed:
            for ch in group.channels:
                seen.setdefault(ch, None)
        return tuple(seen)

    def tile_layout(self) -> tuple[tuple[str, tuple[int, ...]], ...]:
        """Map each displayed group to its column indices in the union buffer.

        Returns ``((label, (col0, col1, ...)), ...)`` where the inner
        ints index the channel axis of the array produced by reading
        ``union_channels()``. The compositor uses this to split a
        single decoded buffer back into per-tile arrays without
        re-reading the file.
        """
        union = self.union_channels()
        index_of = {ch: i for i, ch in enumerate(union)}
        return tuple(
            (g.label, tuple(index_of[c] for c in g.channels))
            for g in self.displayed
        )


def auto_grid(n: int, viewport_aspect: float, tile_aspect: float) -> tuple[int, int]:
    """Choose ``(rows, cols)`` so the contact sheet fills the viewport
    while keeping each tile as close to its native aspect as possible.

    For ``n`` tiles of aspect ``tile_aspect`` (= w/h of the source
    image) inside a viewport of aspect ``viewport_aspect``, the
    column count that minimises wasted space is roughly:

        cols ≈ √(n × viewport_aspect / tile_aspect)

    Round + clamp to ``[1, n]``, then derive rows. Edge cases:

    * ``n == 0`` → ``(1, 1)`` (caller draws nothing anyway).
    * Wide tiles in a square viewport → cols smaller, rows taller.
    * Square tiles in a wide viewport → cols larger.
    """
    if n <= 1:
        return (1, max(1, n))
    if tile_aspect <= 0 or viewport_aspect <= 0:
        # Degenerate inputs (no image yet, headless test). Pick a
        # near-square grid so we don't crash. ``ceil`` so 2 tiles
        # become 1×2 not 1×1.
        cols = max(1, math.ceil(math.sqrt(n)))
    else:
        # ``ceil`` rather than ``round``: when the math is borderline
        # (e.g. n=2 with tile_aspect == viewport_aspect, where 1×2 and
        # 2×1 waste the same area), we'd rather fill the wider axis
        # of the viewport. Round would pick 1×N stacked, which feels
        # wrong in a landscape viewport.
        cols = max(1, math.ceil(math.sqrt(n * viewport_aspect / tile_aspect)))
    cols = min(cols, n)
    rows = math.ceil(n / cols)
    return (rows, cols)

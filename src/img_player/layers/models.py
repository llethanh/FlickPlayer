"""The :class:`Layer` dataclass — one sequence + its position/state.

Pure data, no Qt. Mutation goes through :class:`LayerStack` so
signal emission stays centralised; the dataclass itself is
intentionally mutable (default for ``@dataclass``) because the user
edits offset / trim / visibility live and a frozen-and-replace
pattern would force a stack-wide rebroadcast on each tweak.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from img_player.sequence.channels import ChannelSelection
from img_player.sequence.models import SequenceInfo


def _new_id() -> str:
    """uuid4 hex, used as a stable handle the UI / cache key on."""
    return uuid.uuid4().hex


@dataclass
class Layer:
    """One sequence + its position/state on the master timeline.

    Time positioning model:

    * ``layer_in`` / ``layer_out`` — *source-frame* indices (within
      the underlying ``sequence``). Default to the sequence's full
      range. ``layer_out`` is **inclusive**.
    * ``offset`` — *master-frame* index where ``layer_in`` lands.
      ``offset = 0`` puts the layer's first trimmed frame at master
      frame 0. Negative offsets are allowed (the master timeline's
      lower bound shifts to accommodate).

    So a layer with ``sequence`` 1001-1100, ``layer_in=1010``,
    ``layer_out=1090``, ``offset=50`` covers master frames 50..130
    (= 81 trimmed frames placed starting at master 50).
    """

    sequence: SequenceInfo
    layer_in: int
    layer_out: int
    offset: int = 0
    visible: bool = True
    name: str = ""
    id: str = field(default_factory=_new_id)

    # ---- Per-layer state (populated as the user touches the layer) ----
    channel_selection: ChannelSelection | None = None
    channel_layout_mode: str = "Auto"
    channel_labels_visible: bool = True
    source_colorspace: str | None = None
    exposure: float = 0.0
    gamma: float = 1.0

    # Sidecar paths for annotations + comments. Resolved at
    # construction time relative to ``sequence.directory`` so a layer
    # carries enough info to save its notes without consulting the
    # owning app. ``None`` for layers built before the sidecar layout
    # was decided (defensive — every code path that creates layers
    # should populate these).
    annotations_path: Path | None = None
    comments_path: Path | None = None

    @classmethod
    def from_sequence(
        cls,
        sequence: SequenceInfo,
        offset: int = 0,
        name: str | None = None,
    ) -> Layer:
        """Build a fresh layer from a sequence, defaulting trim to
        the full source range and ``name`` to the display pattern.
        """
        return cls(
            sequence=sequence,
            layer_in=sequence.first_frame,
            layer_out=sequence.last_frame,
            offset=offset,
            name=name or sequence.display_pattern(),
        )

    # ---- Derived properties --------------------------------------------

    @property
    def trim_length(self) -> int:
        """Number of *master* frames this layer covers (= ``layer_out
        - layer_in + 1``, inclusive on both ends)."""
        return max(0, self.layer_out - self.layer_in + 1)

    @property
    def master_start(self) -> int:
        """First master frame this layer occupies."""
        return self.offset

    @property
    def master_end(self) -> int:
        """Last master frame this layer occupies (inclusive)."""
        return self.offset + self.trim_length - 1

    def covers(self, master_frame: int) -> bool:
        """``True`` if this layer has a frame at ``master_frame``.

        Independent of visibility — the LayerStack checks ``visible``
        separately so this method stays useful for "what could be
        shown if I unhid this layer" queries.
        """
        return self.master_start <= master_frame <= self.master_end

    def source_frame_at(self, master_frame: int) -> int:
        """Translate ``master_frame`` to this layer's source-frame.

        Caller must check :meth:`covers` first; out-of-range inputs
        return a frame number outside ``[layer_in, layer_out]``,
        which most downstream code treats as "no decode".
        """
        return self.layer_in + (master_frame - self.master_start)

    # ---- Validation -----------------------------------------------------

    def is_trim_valid(self) -> bool:
        """Whether ``layer_in`` / ``layer_out`` are within the
        underlying sequence's range and well-ordered. The model
        accepts inconsistent values (the UI sometimes mid-edits a
        spinbox); the renderer checks this before issuing a decode.
        """
        return (
            self.sequence.first_frame <= self.layer_in <= self.layer_out
            <= self.sequence.last_frame
        )

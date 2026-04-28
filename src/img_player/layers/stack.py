"""The :class:`LayerStack` — ordered Layers + master-timeline resolution.

The stack is the single source of truth for the multi-layer state.
Every UI surface (layer panel, viewport, channel menu, color panel)
reads from it; every mutation goes through one of its public
methods which then emits a fine-grained signal so listeners only
react to what they care about:

* ``layers_changed`` — composition (add / remove / reorder) — anyone
  drawing the stack visually needs to redraw.
* ``visibility_changed`` — œil toggle — the cache invalidates the
  affected master-frame region; the viewport re-displays.
* ``layer_modified(id)`` — per-layer state mutation (channel,
  colorspace, exposure, trim, offset, name). Listeners that care
  about specific layers (e.g. the layer panel row, the cache) read
  the layer back from the stack on receipt.
* ``focus_changed(id)`` — which layer the user is currently editing.
  The channel menu / color panel / annotation overlay rebind to it.

Order convention: index 0 = top of stack = highest priority. The
class is iterable in that order.
"""

from __future__ import annotations

import logging
from typing import Iterator

from PySide6.QtCore import QObject, Signal

from img_player.layers.models import Layer

log = logging.getLogger(__name__)


class LayerStack(QObject):  # type: ignore[misc]
    """Ordered list of :class:`Layer` + signals on every mutation."""

    # Composition changed (add / remove / reorder). Carries no payload —
    # listeners re-read the full stack via :meth:`layers`.
    layers_changed = Signal()
    # Visibility (œil) toggled on one layer. Carries the layer id so
    # the cache can decide whether to invalidate.
    visibility_changed = Signal(str)
    # Per-layer state mutated (trim, offset, channel, colorspace, …).
    # Carries the layer id; specific signal granularity per field
    # would multiply API surface for little gain.
    layer_modified = Signal(str)
    # The "focused" layer (= what the user is currently editing in
    # the side panels) changed. Empty string when no layer is focused
    # (e.g. all layers were removed).
    focus_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._layers: list[Layer] = []
        self._focused_id: str = ""

    # ------------------------------------------------------------------ Mutation

    def add(self, layer: Layer, position: int = 0) -> None:
        """Insert ``layer`` at ``position`` (default = top of stack).

        Out-of-range positions clamp to the nearest valid index
        rather than raising — drag-drop UX is forgiving and we
        prefer "almost where the user dropped" over exceptions.
        New layer auto-focuses unless one is already focused; that
        way the first layer of a fresh session always becomes
        focused without an extra click, while subsequent adds don't
        steal focus mid-edit.
        """
        position = max(0, min(position, len(self._layers)))
        self._layers.insert(position, layer)
        self.layers_changed.emit()
        if not self._focused_id:
            self.set_focus(layer.id)

    def remove(self, layer_id: str) -> None:
        """Remove the layer with this id. No-op if not found.

        If the focused layer is removed, focus shifts to the next
        layer in stack order (top first), or clears when the stack
        becomes empty.
        """
        for i, layer in enumerate(self._layers):
            if layer.id == layer_id:
                del self._layers[i]
                if self._focused_id == layer_id:
                    new_focus = self._layers[0].id if self._layers else ""
                    self._focused_id = new_focus
                    self.focus_changed.emit(new_focus)
                self.layers_changed.emit()
                return

    def reorder(self, layer_id: str, new_position: int) -> None:
        """Move ``layer_id`` to ``new_position`` in stack order.

        ``new_position`` is the destination index *after* removal —
        so passing ``0`` always lands the layer at the very top
        regardless of its previous position. Out-of-range clamps.
        """
        for i, layer in enumerate(self._layers):
            if layer.id == layer_id:
                if i == new_position:
                    return  # idempotent — no signal
                self._layers.pop(i)
                clamped = max(0, min(new_position, len(self._layers)))
                self._layers.insert(clamped, layer)
                self.layers_changed.emit()
                return

    def toggle_visible(self, layer_id: str) -> None:
        """Flip the œil. Emits :attr:`visibility_changed`."""
        layer = self._find(layer_id)
        if layer is None:
            return
        layer.visible = not layer.visible
        self.visibility_changed.emit(layer_id)

    def set_visible(self, layer_id: str, visible: bool) -> None:
        """Set the œil to a specific value. Idempotent."""
        layer = self._find(layer_id)
        if layer is None or layer.visible == bool(visible):
            return
        layer.visible = bool(visible)
        self.visibility_changed.emit(layer_id)

    def set_focus(self, layer_id: str) -> None:
        """Mark ``layer_id`` as the focused layer. Idempotent.

        Empty string clears focus (no per-layer panel context).
        """
        if layer_id and self._find(layer_id) is None:
            return  # silently ignore unknown id — defensive
        if layer_id == self._focused_id:
            return
        self._focused_id = layer_id
        self.focus_changed.emit(layer_id)

    def update(self, layer_id: str, **fields: object) -> None:
        """Mutate a layer's per-layer state in bulk.

        Each ``fields`` entry must match a Layer attribute name; we
        ``setattr`` each one and emit a single
        :attr:`layer_modified` so multi-field updates (e.g. exposure
        + gamma at once from the color panel) don't fire N signals.
        Unknown attributes are logged and ignored.
        """
        layer = self._find(layer_id)
        if layer is None or not fields:
            return
        for name, value in fields.items():
            if not hasattr(layer, name):
                log.warning("LayerStack.update: unknown field %r", name)
                continue
            setattr(layer, name, value)
        self.layer_modified.emit(layer_id)

    # ------------------------------------------------------------------ Queries

    def __len__(self) -> int:
        return len(self._layers)

    def __iter__(self) -> Iterator[Layer]:
        return iter(self._layers)

    def __bool__(self) -> bool:
        return bool(self._layers)

    def layers(self) -> tuple[Layer, ...]:
        """Snapshot tuple in stack order (top → bottom)."""
        return tuple(self._layers)

    def find(self, layer_id: str) -> Layer | None:
        """Public lookup by id. ``None`` when absent."""
        return self._find(layer_id)

    def focused(self) -> Layer | None:
        """The layer the user is currently editing, or ``None``."""
        if not self._focused_id:
            return None
        return self._find(self._focused_id)

    @property
    def focused_id(self) -> str:
        return self._focused_id

    def topmost_visible_at(self, master_frame: int) -> Layer | None:
        """Return the highest-priority visible layer covering
        ``master_frame``, or ``None`` if every covering layer is
        hidden (or none cover the frame at all → black screen).
        """
        for layer in self._layers:
            if layer.visible and layer.covers(master_frame):
                return layer
        return None

    def covers(self, master_frame: int) -> tuple[Layer, ...]:
        """Every layer that has a frame at ``master_frame`` (any
        visibility). Useful for the cache's pre-fetch policy and for
        the panel's "click visibility" UI."""
        return tuple(layer for layer in self._layers if layer.covers(master_frame))

    def master_range(self) -> tuple[int, int]:
        """Inclusive ``(first, last)`` master frames covered by the
        union of every layer.

        Returns ``(0, 0)`` when the stack is empty — callers that
        care about emptiness should check :meth:`__bool__` first.
        """
        if not self._layers:
            return (0, 0)
        first = min(layer.master_start for layer in self._layers)
        last = max(layer.master_end for layer in self._layers)
        return (first, last)

    def master_length(self) -> int:
        """Total span of the master timeline in frames."""
        if not self._layers:
            return 0
        first, last = self.master_range()
        return last - first + 1

    # ------------------------------------------------------------------ Internals

    def _find(self, layer_id: str) -> Layer | None:
        for layer in self._layers:
            if layer.id == layer_id:
                return layer
        return None

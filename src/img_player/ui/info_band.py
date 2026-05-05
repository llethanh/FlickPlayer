"""Bottom-anchored info band over the viewer.

Semi-transparent orange strip showing per-segment readouts (layer
name, image dimensions, fps, layer-local frame, master timeline
frame). Inspired by PDPlayer's bottom HUD. Lives inside the display
area (child of :class:`ViewerWidget`) so it sits *above* the
timeline, not inside it.

The user picks which segments are visible via the right-click menu
on the ⓘ pill in the timeline gutter. Default: all five on.

The band is mouse-transparent — clicks fall through to the GL
viewport so drag-scrub keeps working when the band is shown.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from img_player.ui.frame_display import _frame_to_timecode

DisplayMode = str  # "frames" | "tc" — same vocabulary as Timeline / TransportBar.

# Stable order in which segments appear left-to-right when visible.
# Also the keys used by the context menu / preferences serialiser.
SEGMENT_KEYS: tuple[str, ...] = ("name", "size", "fps", "local", "global")
SEGMENT_LABELS: dict[str, str] = {
    "name": "Layer name",
    "size": "Image size",
    "fps": "Frame rate",
    "local": "Layer frame",
    "global": "Timeline frame",
}


class InfoBand(QWidget):  # type: ignore[misc]
    """Orange bottom HUD — readouts separated by thin dividers."""

    HEIGHT = 22  # px — kept compact so it doesn't crop the image too much

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Required for stylesheet ``background:`` to actually paint
        # on a custom QWidget subclass. Without this the rule below
        # only colours the QLabel children and the band itself stays
        # transparent — image visible underneath in patches.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Background: ACCENT (#E8901C) at ~55% alpha. Text in warm
        # cream (#FFE5C0) reads as part of the band rather than
        # fighting it.
        self.setStyleSheet(
            "InfoBand {"
            "  background: rgba(232, 144, 28, 140);"
            "}"
            "QLabel {"
            "  color: #FFE5C0;"
            "  font-family: 'Consolas', 'Menlo', 'JetBrains Mono', monospace;"
            "  font-size: 11px;"
            "  font-weight: 600;"
            "  background: transparent;"
            "}"
            "QLabel#sep {"
            "  color: rgba(255, 229, 192, 110);"
            "  font-weight: 400;"
            "}"
        )
        self.setFixedHeight(self.HEIGHT)

        # One label per segment. They're all created up-front and
        # added to / removed from the layout depending on the user's
        # visibility choices.
        self._labels: dict[str, QLabel] = {
            key: QLabel("—", self) for key in SEGMENT_KEYS
        }
        # Visibility state — defaults to "everything on". Mutated via
        # :meth:`set_segment_visible`; read back via
        # :meth:`is_segment_visible` / :meth:`visible_segments`.
        self._visible: dict[str, bool] = {key: True for key in SEGMENT_KEYS}

        # Cached state — needed when ``set_display_mode`` / ``set_fps``
        # change and we want to re-render without waiting for the
        # next frame_changed tick.
        self._mode: DisplayMode = "frames"
        self._fps: float = 24.0
        self._local_cur: int | None = None
        self._local_max: int | None = None
        self._global_cur: int | None = None
        self._global_max: int | None = None

        # Layout is rebuilt by :meth:`_relayout` on every visibility
        # change — separators are inserted between consecutive
        # visible segments, never before the first or after the last.
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(10, 0, 10, 0)
        self._lay.setSpacing(8)
        self._relayout()

    # ---- Public API ---------------------------------------------------

    def set_image_size(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            self._labels["size"].setText("— × —")
        else:
            self._labels["size"].setText(f"{width} × {height}")

    def set_fps(self, fps: float | None) -> None:
        if fps is None or fps <= 0:
            self._labels["fps"].setText("— fps")
        else:
            self._labels["fps"].setText(f"{fps:.3f} fps")
        # The timecode formatter needs the fps too — keep it cached
        # so a TC re-render doesn't have to chase the controller.
        if fps is not None and fps > 0:
            self._fps = float(fps)
            if self._mode == "tc":
                # Repaint with new fps mapping without waiting for the
                # next playhead tick.
                self._render_local()
                self._render_global()

    def set_layer_name(self, name: str | None) -> None:
        """Display ``name`` in the layer-name segment. ``None`` /
        empty string blanks the readout (= no covering layer)."""
        if not name:
            self._labels["name"].setText("—")
        else:
            self._labels["name"].setText(name)

    def set_display_mode(self, mode: DisplayMode) -> None:
        """Switch between frame numbers and timecode for the layer /
        frame readouts. Same vocabulary as Timeline / TransportBar."""
        if mode == self._mode:
            return
        self._mode = mode
        self._render_local()
        self._render_global()

    def set_local_frame(self, current: int | None, total: int | None) -> None:
        """Source-frame number of the topmost visible layer at the
        playhead, plus the layer's last source frame. Numbers match
        what the user sees in the file names on disk.

        ``None`` for either side blanks out (no layer covering the
        playhead, e.g. inside a gap).
        """
        self._local_cur, self._local_max = current, total
        self._render_local()

    def set_global_frame(self, current: int | None, total: int | None) -> None:
        """Absolute master timeline frame and the timeline's last
        frame. Mirrors the values shown on the timeline ticks and the
        transport's frame readout."""
        self._global_cur, self._global_max = current, total
        self._render_global()

    def set_segment_visible(self, key: str, visible: bool) -> None:
        """Show / hide one of the ``SEGMENT_KEYS`` readouts."""
        if key not in self._visible or self._visible[key] == visible:
            return
        self._visible[key] = bool(visible)
        self._relayout()

    def is_segment_visible(self, key: str) -> bool:
        return bool(self._visible.get(key, False))

    def visible_segments(self) -> tuple[str, ...]:
        """Snapshot of which segments are currently visible (used to
        persist state in preferences)."""
        return tuple(k for k in SEGMENT_KEYS if self._visible.get(k, False))

    def set_visible_segments(self, keys) -> None:  # type: ignore[no-untyped-def]
        """Bulk-replace visibility — convenience for prefs restore.
        ``keys`` is any iterable of segment keys; missing keys are
        treated as hidden, unknown keys ignored."""
        wanted = {k for k in keys if k in self._visible}
        changed = False
        for k in SEGMENT_KEYS:
            new = k in wanted
            if self._visible[k] != new:
                self._visible[k] = new
                changed = True
        if changed:
            self._relayout()

    # ---- Internal -----------------------------------------------------

    def _make_separator(self) -> QLabel:
        s = QLabel("│", self)
        s.setObjectName("sep")
        return s

    def _relayout(self) -> None:
        """Rebuild the row's child widgets in the canonical order,
        inserting fresh separator labels between consecutive visible
        segments. Removed widgets stay alive (they're owned by the
        InfoBand instance) so the next ``_relayout`` re-uses them.

        Hiding through layout-rebuild rather than ``setVisible`` so
        separators never end up dangling at row edges."""
        # Drop every existing layout item but keep the persistent
        # segment labels alive. Separators are throwaway QLabels — we
        # delete them so they don't accumulate.
        while self._lay.count():
            item = self._lay.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            if widget in self._labels.values():
                widget.setParent(self)  # detach from layout, keep alive
            else:
                widget.deleteLater()
        first = True
        for key in SEGMENT_KEYS:
            if not self._visible.get(key, False):
                self._labels[key].setParent(self)  # off-layout, hidden
                self._labels[key].hide()
                continue
            if not first:
                self._lay.addWidget(self._make_separator())
            self._labels[key].show()
            self._lay.addWidget(self._labels[key])
            first = False
        self._lay.addStretch(1)

    def _render_local(self) -> None:
        self._labels["local"].setText(
            self._format_pair("Layer", self._local_cur, self._local_max),
        )

    def _render_global(self) -> None:
        self._labels["global"].setText(
            self._format_pair("Frame", self._global_cur, self._global_max),
        )

    def _format_pair(
        self, label: str, current: int | None, total: int | None,
    ) -> str:
        if current is None or total is None or total <= 0:
            return f"{label}  — / —"
        if self._mode == "tc":
            return (
                f"{label}  "
                f"{_frame_to_timecode(current, self._fps)} / "
                f"{_frame_to_timecode(total, self._fps)}"
            )
        width = len(str(total))
        return f"{label}  {current:0{width}d} / {total}"

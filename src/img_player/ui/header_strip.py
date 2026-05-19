"""Header info strip — brief §2.

A thin (26 px) horizontal band that sits between the menu bar and
the viewport. Shows the loaded sequence's identity + current
playhead position in a single readable row, formatted in JetBrains
Mono with the warm-amber accent applied at low alpha so it reads
as a "VFX info cartouche" rather than a chrome bar.

Cells (left → right, separated by 1 px ``BORDER_ACC_DEEP`` hairlines):

1. **Sequence name** (weight 600, flex-grow 1) — the display
   pattern such as ``CLSH_SEQ001_SH0020_CMP_Render_Output.####.png``.
2. **Resolution** — ``1920×1080``.
3. **FPS** — ``25.000 fps``.
4. **Layer range** — ``Layer 1001/1033`` (current layer's local
   frame within its trim).
5. **Frame range** — ``Frame 1001/1244`` (current master frame
   within the broad navigable range).

The widget exposes both granular setters (so a frame-change tick
only updates the relevant cell) and a one-shot
:meth:`set_sequence` helper called on load.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from img_player.ui.theme import F, G, H, S


class _HairlineSep(QFrame):  # type: ignore[misc]
    """1-px vertical separator in BORDER_ACC_DEEP — the line between
    cells of the info strip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(1)
        self.setStyleSheet(f"background-color: {H.BORDER_ACC_DEEP};")


class HeaderInfoStrip(QWidget):  # type: ignore[misc]
    """Thin header band that surfaces the loaded sequence's metadata.

    Hidden when no sequence is loaded — call :meth:`set_visible_for_sequence`
    on load (typically from ``MainWindow.update_sequence_info``) so the
    strip appears, then keep it updated via :meth:`set_frame_position`
    on each playhead change.
    """

    HEIGHT = G.CTRL_BUTTON_H - 2  # 26 px per brief §2
    # Padding inside each cell, brief §2: padding-x 14.
    CELL_PAD_H = S.S_14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("headerInfoStrip")
        self.setFixedHeight(self.HEIGHT)
        # Strip background: warm-amber at 10% alpha + 1 px solid
        # accent-deep border + 3 px radius. Same chrome as the legacy
        # info-band over the viewer, just lifted into a top-of-window
        # cartouche.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#headerInfoStrip {{"
            f"  background-color: {H.ACC_TINT_10};"
            f"  border: 1px solid {H.BORDER_ACC_DEEP};"
            f"  border-radius: {G.RADIUS_MD}px;"
            f"}}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- Cell 1 — Sequence name (weight 600, flex 1) ----------------
        self._name_label = self._make_cell_label(
            weight_600=True, expand=True, color=H.ACC_BRIGHT,
        )
        self._name_label.setText("(no sequence)")
        layout.addWidget(self._name_label, 1)

        # ---- Cell 2 — Resolution ---------------------------------------
        layout.addWidget(_HairlineSep(self))
        self._res_label = self._make_cell_label()
        layout.addWidget(self._res_label, 0)

        # ---- Cell 3 — FPS ----------------------------------------------
        layout.addWidget(_HairlineSep(self))
        self._fps_label = self._make_cell_label()
        layout.addWidget(self._fps_label, 0)

        # ---- Cell 4 — Layer position -----------------------------------
        # "Layer NN/NN" with a dim "Layer" prefix.
        layout.addWidget(_HairlineSep(self))
        self._layer_label = self._make_kv_label(prefix="Layer")
        layout.addWidget(self._layer_label, 0)

        # ---- Cell 5 — Frame position -----------------------------------
        layout.addWidget(_HairlineSep(self))
        self._frame_label = self._make_kv_label(prefix="Frame")
        layout.addWidget(self._frame_label, 0)

        # Hidden until the first sequence loads — the empty cartouche
        # at boot would be visual noise on the "no project" state.
        self.setVisible(False)

    # ------------------------------------------------------------------ Helpers

    def _make_cell_label(
        self,
        *,
        weight_600: bool = False,
        expand: bool = False,
        color: str | None = None,
    ) -> QLabel:
        label = QLabel(self)
        # Padding-x baked into the QSS so each cell visually owns the
        # space between its hairline separators.
        weight = 600 if weight_600 else 500
        col = color or H.ACC_BRIGHT
        label.setStyleSheet(
            f"color: {col};"
            f"font-family: {F.FAMILY_MONO};"
            f"font-size: {F.SIZE_MONO_CODE}px;"
            f"font-weight: {weight};"
            f"padding: 0 {self.CELL_PAD_H}px;"
        )
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if expand:
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
            )
            # Truncate the sequence name with an ellipsis rather than
            # forcing the strip wider — narrow windows still get a
            # consistent layout, the user opens a tooltip if they need
            # the full name.
            label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        return label

    def _make_kv_label(self, *, prefix: str) -> QLabel:
        """Build a cell that renders ``<dim>prefix</dim> value``.

        Uses ``RichText`` so the prefix and the value can carry
        different alpha levels without splitting the cell into two
        separate widgets.
        """
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.RichText)
        # The padding/alignment is the same as a plain cell label, but
        # we drive the colour via inline spans rather than a global
        # ``color:`` rule (the two spans use different alphas).
        label.setStyleSheet(
            f"font-family: {F.FAMILY_MONO};"
            f"font-size: {F.SIZE_MONO_CODE}px;"
            f"font-weight: 500;"
            f"padding: 0 {self.CELL_PAD_H}px;"
        )
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Store the prefix so :meth:`_render_kv` can rebuild the HTML.
        label.setProperty("kvPrefix", prefix)
        # Initial empty state — render with no value so the cell is
        # visually present even before the first frame change.
        self._render_kv(label, "")
        return label

    @staticmethod
    def _render_kv(label: QLabel, value: str) -> None:
        prefix = label.property("kvPrefix") or ""
        # The prefix sits at ~65 % alpha of the accent bright so the
        # value reads as the dominant info.
        html = (
            f"<span style='color: rgba(245, 168, 48, 0.65);'>{prefix}</span>"
            f"&nbsp;&nbsp;"
            f"<span style='color: {H.ACC_BRIGHT};'>{value}</span>"
        )
        label.setText(html)

    # ------------------------------------------------------------------ Public API

    def set_sequence_name(self, name: str) -> None:
        """Update cell 1 — the sequence's display pattern.

        Long names are elided by Qt's label rendering when the cell
        is narrower than the text; full name surfaces via the
        tooltip.
        """
        self._name_label.setText(name or "(no sequence)")
        self._name_label.setToolTip(name)

    def set_resolution(self, width: int | None, height: int | None) -> None:
        """Update cell 2. Pass ``None``/``None`` to clear (renders as
        ``—``)."""
        if width and height:
            self._res_label.setText(f"{int(width)}×{int(height)}")
        else:
            self._res_label.setText("—")

    def set_fps(self, fps: float | None) -> None:
        """Update cell 3. Accepts ``None`` to clear."""
        if fps and fps > 0:
            self._fps_label.setText(f"{float(fps):.3f} fps")
        else:
            self._fps_label.setText("—")

    def set_layer_position(self, current: int, total: int) -> None:
        """Update cell 4 (Layer NN/NN)."""
        self._render_kv(self._layer_label, f"{int(current)}/{int(total)}")

    def set_frame_position(self, current: int, total: int) -> None:
        """Update cell 5 (Frame NN/NN). Called on every playhead change."""
        self._render_kv(self._frame_label, f"{int(current)}/{int(total)}")

    def set_visible_for_sequence(self, has_sequence: bool) -> None:
        """Show / hide the strip based on whether a sequence is loaded.

        Called from ``MainWindow.update_sequence_info`` (on load) and
        from the New / detach paths (to hide). Equivalent to
        ``setVisible(has_sequence)`` — wrapped under a named method
        so the call sites read clearly.
        """
        self.setVisible(bool(has_sequence))

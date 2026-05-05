"""Top-of-viewer band for the two-layer compare overlay.

UI surface for picking layers A / B, the compare mode (swap / vert /
horiz / opacity) and the seam position. Sits as a child of the
:class:`ViewerWidget` so it floats above the GL viewport in absolute
coords; visibility is toggled by ``app.py`` based on
``CompareState.enabled``.

Pure UI — emits signals, doesn't own the state. The owning app is
the single source of truth and re-feeds the band on every state
change so a programmatic update (session load, keyboard shortcut)
keeps the widget in sync without bespoke setters.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from img_player.compare.state import (
    COMPARE_MODES,
    MODE_HORIZONTAL,
    MODE_OPACITY,
    MODE_VERTICAL,
)
from img_player.ui.theme import G, H, S


# Friendly labels for the three blend modes — kept short so the band
# stays compact at smaller viewport widths.
_MODE_LABELS: dict[str, str] = {
    MODE_VERTICAL: "Vert",
    MODE_HORIZONTAL: "Horiz",
    MODE_OPACITY: "Opacity",
}


@dataclass(frozen=True)
class _LayerOption:
    """One entry in either A or B dropdown."""

    layer_id: str
    name: str


class CompareBand(QFrame):  # type: ignore[misc]
    """Floating band: ``[A ▼]  [Swap | Vert | Horiz | Opacity]  ──●── 50%  ✕``."""

    # User picked a different layer in dropdown A or B. The app
    # writes the new id into CompareState and triggers a redraw.
    layer_a_picked = Signal(str)
    layer_b_picked = Signal(str)
    # User clicked one of the four mode buttons. Carries the mode
    # token (one of :data:`COMPARE_MODES`).
    mode_picked = Signal(str)
    # Seam slider moved (0..100 → 0.0..1.0 on the receiver side).
    # Continuous: emits while dragging. The viewer redraws live.
    seam_changed = Signal(float)
    # User clicked the always-visible "Solo B" toggle. The receiver
    # flips ``CompareState.swap_showing_b`` and re-renders.
    swap_toggled = Signal()
    # User clicked ✕ — exit compare mode entirely.
    close_requested = Signal()
    # User clicked ⇄ — permute A and B.
    swap_layers_requested = Signal()

    BAND_HEIGHT = G.INPUT_H + 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("compareBand")
        self.setFrameShape(QFrame.Shape.NoFrame)
        # Solid dark background slightly transparent so a hint of
        # image colour shows through at the band edges (helps the
        # user remember the band sits ON the viewer, not next to it).
        self.setStyleSheet(
            "QFrame#compareBand { "
            "background: rgba(15, 17, 21, 230); "
            f"border-bottom: 1px solid {H.BORDER_DEFAULT}; "
            "}"
        )
        self.setFixedHeight(self.BAND_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(S.SM, 4, S.SM, 4)
        layout.setSpacing(S.SM)

        # ---- Layer A dropdown ----
        layout.addWidget(QLabel("A"))
        self._combo_a = QComboBox()
        self._combo_a.setFixedHeight(G.INPUT_H)
        self._combo_a.setMinimumWidth(140)
        self._combo_a.activated.connect(self._on_a_activated)
        layout.addWidget(self._combo_a)

        # ⇄ swap layers button (permute A/B in the dropdowns).
        self._swap_layers_btn = QPushButton("⇄")
        self._swap_layers_btn.setFixedSize(G.INPUT_H, G.INPUT_H)
        self._swap_layers_btn.setToolTip("Swap layers (Ctrl+W)")
        self._swap_layers_btn.clicked.connect(self.swap_layers_requested.emit)
        layout.addWidget(self._swap_layers_btn)

        # ---- Layer B dropdown ----
        layout.addWidget(QLabel("B"))
        self._combo_b = QComboBox()
        self._combo_b.setFixedHeight(G.INPUT_H)
        self._combo_b.setMinimumWidth(140)
        self._combo_b.activated.connect(self._on_b_activated)
        layout.addWidget(self._combo_b)

        # ---- Mode buttons (mutually exclusive) ----
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_buttons: dict[str, QPushButton] = {}
        for mode in COMPARE_MODES:
            btn = QPushButton(_MODE_LABELS[mode])
            btn.setCheckable(True)
            btn.setFixedHeight(G.INPUT_H)
            btn.clicked.connect(lambda _checked, m=mode: self.mode_picked.emit(m))
            self._mode_group.addButton(btn)
            self._mode_buttons[mode] = btn
            layout.addWidget(btn)

        # ---- Seam slider ----
        self._seam_slider = QSlider(Qt.Orientation.Horizontal)
        self._seam_slider.setRange(0, 100)
        self._seam_slider.setValue(50)
        self._seam_slider.setFixedWidth(120)
        self._seam_slider.valueChanged.connect(self._on_seam_changed)
        layout.addWidget(self._seam_slider)
        self._seam_readout = QLabel("50%")
        self._seam_readout.setFixedWidth(36)
        self._seam_readout.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        layout.addWidget(self._seam_readout)

        # ---- Solo B toggle (always visible) ----
        # Checkable: when down, ``swap_showing_b`` is True and the
        # compose path returns full B regardless of the blend mode.
        # When up, the picked blend mode + slider apply normally.
        # Useful for A/B-spot-checking subtle differences without
        # leaving the current wipe configuration.
        self._swap_btn = QPushButton("A↔B")
        self._swap_btn.setCheckable(True)
        self._swap_btn.setFixedHeight(G.INPUT_H)
        self._swap_btn.setToolTip(
            "Show full B (override mode) — click again to return to the blend",
        )
        self._swap_btn.clicked.connect(self.swap_toggled.emit)
        layout.addWidget(self._swap_btn)

        # Push the close button to the far right.
        layout.addStretch(1)

        # ---- ✕ close ----
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(G.INPUT_H, G.INPUT_H)
        self._close_btn.setToolTip("Exit compare mode (W)")
        self._close_btn.clicked.connect(self.close_requested.emit)
        layout.addWidget(self._close_btn)

    # ------------------------------------------------------------------ Public API

    def set_available_layers(
        self, options: list[_LayerOption], *,
        a_id: str | None, b_id: str | None,
    ) -> None:
        """Repopulate both dropdowns from the layer stack.

        Called whenever ``layers_changed`` fires. Block signals so
        the rebuild doesn't trigger ``layer_a_picked`` /
        ``layer_b_picked`` emissions for a non-user change.
        """
        for combo, current in (
            (self._combo_a, a_id), (self._combo_b, b_id),
        ):
            combo.blockSignals(True)
            combo.clear()
            for opt in options:
                combo.addItem(opt.name, opt.layer_id)
            if current is not None:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def set_mode(self, mode: str) -> None:
        """Update the checked mode button without firing ``mode_picked``."""
        btn = self._mode_buttons.get(mode)
        if btn is None:
            return
        btn.blockSignals(True)
        btn.setChecked(True)
        btn.blockSignals(False)
        # Slider + Solo-B toggle are always available regardless of
        # the picked mode (Vert / Horiz / Opacity); no per-mode
        # gating since the contact-sheet retirement.

    def set_swap_showing_b(self, on: bool) -> None:
        """Sync the Solo-B button's checked state from outside."""
        self._swap_btn.blockSignals(True)
        self._swap_btn.setChecked(bool(on))
        self._swap_btn.blockSignals(False)

    def set_seam(self, seam: float) -> None:
        """Sync the slider with an externally-changed seam (drag in
        viewport, keyboard nudge, session load). Clamped to [0, 1]."""
        clamped = max(0.0, min(1.0, float(seam)))
        value = int(round(clamped * 100))
        self._seam_slider.blockSignals(True)
        self._seam_slider.setValue(value)
        self._seam_slider.blockSignals(False)
        self._seam_readout.setText(f"{value}%")

    # ------------------------------------------------------------------ Internals

    def _on_a_activated(self, index: int) -> None:
        layer_id = self._combo_a.itemData(index)
        if isinstance(layer_id, str):
            self.layer_a_picked.emit(layer_id)

    def _on_b_activated(self, index: int) -> None:
        layer_id = self._combo_b.itemData(index)
        if isinstance(layer_id, str):
            self.layer_b_picked.emit(layer_id)

    def _on_seam_changed(self, value: int) -> None:
        # QSlider emits int 0..100 → normalise to 0.0..1.0.
        self._seam_readout.setText(f"{value}%")
        self.seam_changed.emit(value / 100.0)

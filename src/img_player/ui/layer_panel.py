"""LayerPanel — collapsible list of :class:`Layer` rows below the timeline.

Drawn as a vertical stack of :class:`LayerRow` widgets, with a tiny
header (chevron + count) that lets the user fold the panel away to
reclaim viewport vertical space.

The panel is **always present** in the main window (per Q10/A) — it
just collapses to its header when no layer is loaded or when the user
hides it manually. Single-sequence playback shows one row, mirroring
the behaviour of multi-layer setups so there's no special-case UI
when going from 1 to 2 layers.

Phase 3 scope (this commit): rows + visibility toggle + reorder via
buttons. The bar visualisation on the master timeline (offset / trim
drag handles) lands in phase 4.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from img_player.layers import Layer, LayerStack
from img_player.ui.theme import C, F, G, H, S


# ---------------------------------------------------------------- LayerRow

# Widget heights — kept tight so the panel stays unobtrusive when
# multiple layers stack up. Tuned to match the existing transport-bar
# button heights for visual continuity.
_ROW_HEIGHT = 26
_NUMBER_W = 26       # leftmost "#" column
_EYE_W = 26          # visibility toggle
_BUTTONS_W = 22      # ↑ / ↓ reorder buttons (one width each)


class LayerRow(QFrame):  # type: ignore[misc]
    """One row in the panel: number + eye + name + reorder buttons.

    Highlights itself when the layer it represents is the focused
    layer (= the one the user is currently editing). Clicking
    anywhere on the row sets focus.
    """

    # Mouse-press anywhere on the row asks the panel to focus this
    # layer. The panel forwards to LayerStack.set_focus.
    focus_requested = Signal(str)
    # Eye toggle — the panel just routes to LayerStack.toggle_visible.
    visibility_toggle_requested = Signal(str)
    # Move this row up / down in the stack. Carries the layer id.
    move_up_requested = Signal(str)
    move_down_requested = Signal(str)

    def __init__(self, layer: Layer, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layer_id = layer.id
        self.setFixedHeight(_ROW_HEIGHT)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAutoFillBackground(True)
        # Click anywhere = focus.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(S.SM, 0, S.SM, 0)
        layout.setSpacing(S.SM)

        # --- Layer number ----------------------------------------------
        self._number_label = QLabel(str(index + 1))
        self._number_label.setFixedWidth(_NUMBER_W)
        self._number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._number_label.setFont(F.mono(F.SIZE_SM))
        layout.addWidget(self._number_label)

        # --- Eye / visibility ------------------------------------------
        # Plain text "eye" emoji works for now; can swap for an SVG
        # icon later. Checkable button for visual feedback.
        self._eye_btn = QToolButton()
        self._eye_btn.setFixedSize(_EYE_W, _ROW_HEIGHT - 4)
        self._eye_btn.setCheckable(True)
        self._eye_btn.setChecked(layer.visible)
        self._eye_btn.setText("👁" if layer.visible else "·")
        self._eye_btn.setToolTip("Show / hide this layer")
        self._eye_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._eye_btn.clicked.connect(self._on_eye_clicked)
        layout.addWidget(self._eye_btn)

        # --- Name (filename pattern) -----------------------------------
        self._name_label = QLabel(layer.name)
        self._name_label.setMinimumWidth(120)
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        self._name_label.setFont(F.mono(F.SIZE_SM))
        layout.addWidget(self._name_label, 1)

        # --- Reorder buttons (↑ / ↓) -----------------------------------
        # Phase 3 keyboard-free path. Drag-to-reorder lands in a
        # later phase; for now these buttons cover the use case.
        self._up_btn = QToolButton()
        self._up_btn.setFixedSize(_BUTTONS_W, _ROW_HEIGHT - 4)
        self._up_btn.setText("↑")
        self._up_btn.setToolTip("Move layer up (higher priority)")
        self._up_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._up_btn.clicked.connect(
            lambda: self.move_up_requested.emit(self._layer_id),
        )
        layout.addWidget(self._up_btn)

        self._down_btn = QToolButton()
        self._down_btn.setFixedSize(_BUTTONS_W, _ROW_HEIGHT - 4)
        self._down_btn.setText("↓")
        self._down_btn.setToolTip("Move layer down (lower priority)")
        self._down_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._down_btn.clicked.connect(
            lambda: self.move_down_requested.emit(self._layer_id),
        )
        layout.addWidget(self._down_btn)

        # Default unfocused look. ``set_focused(True)`` paints the
        # accent background to match PDPlayer / Nuke conventions.
        self._focused = False
        self._refresh_palette()

    # ------------------------------------------------------------------ Public API

    @property
    def layer_id(self) -> str:
        return self._layer_id

    def set_index(self, index: int) -> None:
        """Update the leftmost layer number after a reorder."""
        self._number_label.setText(str(index + 1))

    def set_visible_state(self, visible: bool) -> None:
        """Sync the eye button without retriggering the signal."""
        self._eye_btn.blockSignals(True)
        self._eye_btn.setChecked(bool(visible))
        self._eye_btn.setText("👁" if visible else "·")
        self._eye_btn.blockSignals(False)

    def set_name(self, name: str) -> None:
        self._name_label.setText(name)

    def set_focused(self, on: bool) -> None:
        if on == self._focused:
            return
        self._focused = on
        self._refresh_palette()

    def set_can_move_up(self, on: bool) -> None:
        self._up_btn.setEnabled(on)

    def set_can_move_down(self, on: bool) -> None:
        self._down_btn.setEnabled(on)

    # ------------------------------------------------------------------ Internals

    def _refresh_palette(self) -> None:
        """Apply the focused/unfocused background tint."""
        if self._focused:
            self.setStyleSheet(
                f"QFrame {{ background: {H.ACCENT_DIM}; }}"
                f"QLabel {{ color: #FFF; }}"
            )
        else:
            self.setStyleSheet(
                "QFrame { background: transparent; }"
                "QLabel { color: #C8C8C8; }"
            )

    def _on_eye_clicked(self) -> None:
        # Toggle the glyph immediately for snappy feedback; the
        # actual mutation flows back via the LayerStack signal.
        new_visible = self._eye_btn.isChecked()
        self._eye_btn.setText("👁" if new_visible else "·")
        self.visibility_toggle_requested.emit(self._layer_id)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        # Click anywhere on the row (outside its sub-buttons) =
        # focus this layer.
        if event.button() == Qt.MouseButton.LeftButton:
            self.focus_requested.emit(self._layer_id)
        super().mousePressEvent(event)


# ---------------------------------------------------------------- LayerPanel


_HEADER_H = 22
_PANEL_BG = "#0E0F12"
_PANEL_HEADER_BG = "#16181D"


class LayerPanel(QFrame):  # type: ignore[misc]
    """Collapsible list of LayerRows + a header with a chevron toggle.

    Reads from a :class:`LayerStack` and rebuilds itself on every
    composition change. The widget is owned by :class:`MainWindow`,
    parented under the timeline.
    """

    def __init__(
        self,
        stack: LayerStack,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._stack = stack
        self._collapsed = False
        self._rows: dict[str, LayerRow] = {}

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(f"LayerPanel {{ background: {_PANEL_BG}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- Header (collapse chevron + count) --------------------------
        self._header = self._build_header()
        outer.addWidget(self._header)

        # --- Rows container --------------------------------------------
        # Plain QWidget that hosts a QVBoxLayout populated dynamically.
        # When collapsed, this widget is hidden — the header alone
        # remains visible at ``_HEADER_H`` px.
        self._rows_host = QWidget(self)
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(1)
        outer.addWidget(self._rows_host, 1)

        self._stack.layers_changed.connect(self._rebuild)
        self._stack.visibility_changed.connect(self._on_visibility_changed)
        self._stack.layer_modified.connect(self._on_layer_modified)
        self._stack.focus_changed.connect(self._on_focus_changed)

        self._rebuild()

    # ------------------------------------------------------------------ Public API

    def set_collapsed(self, on: bool) -> None:
        """Show / hide the rows; header stays visible."""
        if on == self._collapsed:
            return
        self._collapsed = bool(on)
        self._rows_host.setVisible(not self._collapsed)
        self._chevron_btn.setText("▸" if self._collapsed else "▾")

    def is_collapsed(self) -> bool:
        return self._collapsed

    # ------------------------------------------------------------------ Internals

    def _build_header(self) -> QWidget:
        header = QFrame(self)
        header.setFixedHeight(_HEADER_H)
        header.setStyleSheet(f"QFrame {{ background: {_PANEL_HEADER_BG}; }}")
        h = QHBoxLayout(header)
        h.setContentsMargins(S.SM, 0, S.SM, 0)
        h.setSpacing(S.SM)

        self._chevron_btn = QToolButton(header)
        self._chevron_btn.setFixedSize(18, 18)
        self._chevron_btn.setText("▾")
        self._chevron_btn.setToolTip("Show / hide layers")
        self._chevron_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chevron_btn.clicked.connect(
            lambda: self.set_collapsed(not self._collapsed),
        )
        h.addWidget(self._chevron_btn)

        title = QLabel("Layers")
        title.setStyleSheet("color: #B0B0B0;")
        title.setFont(F.ui(F.SIZE_SM))
        h.addWidget(title)

        h.addStretch(1)

        self._count_label = QLabel("0")
        self._count_label.setStyleSheet("color: #707070;")
        self._count_label.setFont(F.mono(F.SIZE_XS))
        self._count_label.setToolTip("Number of layers")
        h.addWidget(self._count_label)

        return header

    def _rebuild(self) -> None:
        """Throw away every row and rebuild from the stack snapshot.

        Cheap because LayerRow construction is just a few QLabels;
        if profiling ever shows this hot we can switch to in-place
        update of existing rows.
        """
        # Clear existing rows.
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows.clear()

        layers = self._stack.layers()
        self._count_label.setText(str(len(layers)))
        focused_id = self._stack.focused_id

        for i, layer in enumerate(layers):
            row = LayerRow(layer, index=i, parent=self._rows_host)
            row.set_focused(layer.id == focused_id)
            row.set_can_move_up(i > 0)
            row.set_can_move_down(i < len(layers) - 1)
            row.focus_requested.connect(self._on_row_focus_requested)
            row.visibility_toggle_requested.connect(self._on_row_visibility_toggle)
            row.move_up_requested.connect(self._on_row_move_up)
            row.move_down_requested.connect(self._on_row_move_down)
            self._rows_layout.addWidget(row)
            self._rows[layer.id] = row

        # Empty stack → still draw an empty hint so the panel
        # doesn't look broken.
        if not layers:
            empty = QLabel("No layer loaded — drop a sequence onto the viewer.")
            empty.setStyleSheet("color: #606060; padding: 6px 12px;")
            empty.setFont(F.ui(F.SIZE_XS))
            self._rows_layout.addWidget(empty)

    def _on_visibility_changed(self, layer_id: str) -> None:
        layer = self._stack.find(layer_id)
        row = self._rows.get(layer_id)
        if layer is not None and row is not None:
            row.set_visible_state(layer.visible)

    def _on_layer_modified(self, layer_id: str) -> None:
        layer = self._stack.find(layer_id)
        row = self._rows.get(layer_id)
        if layer is not None and row is not None:
            row.set_name(layer.name)

    def _on_focus_changed(self, layer_id: str) -> None:
        for lid, row in self._rows.items():
            row.set_focused(lid == layer_id)

    def _on_row_focus_requested(self, layer_id: str) -> None:
        self._stack.set_focus(layer_id)

    def _on_row_visibility_toggle(self, layer_id: str) -> None:
        self._stack.toggle_visible(layer_id)

    def _on_row_move_up(self, layer_id: str) -> None:
        for i, layer in enumerate(self._stack.layers()):
            if layer.id == layer_id and i > 0:
                self._stack.reorder(layer_id, i - 1)
                return

    def _on_row_move_down(self, layer_id: str) -> None:
        layers = self._stack.layers()
        for i, layer in enumerate(layers):
            if layer.id == layer_id and i < len(layers) - 1:
                self._stack.reorder(layer_id, i + 1)
                return

"""Checkable popup menu for channel selection (single + contact-sheet).

Replaces the legacy ``QComboBox`` in the transport bar so the user can
both pick a single "active" group (= the legacy single-display mode)
*and* tick one or more groups for the contact-sheet view. The widget
emits a single :class:`ChannelSelection` whenever either dimension
changes, plus a separate signal for the chosen grid layout (Auto /
1×N / N×1 / 2×2 / 3×3 / 4×4).

UI structure:

    ┌── ChannelMenu (QMenu) ──────────────────────┐
    │  ●  RGB                          [✓]        │
    │  ○  albedo                       [ ]        │
    │  ○  diffuse                      [✓]        │
    │  ○  Z                            [ ]        │
    │  ──────────────────────────────────         │
    │  Layout :  [Auto ▼]                         │
    │  [Reset all ]    [Close]                    │
    └─────────────────────────────────────────────┘

Each row is a :class:`_ChannelRow` (QWidget wrapped in a
:class:`QWidgetAction`). The footer is three more QWidgetActions for
the layout combo and the two action buttons.

Why a real ``QMenu`` (vs a top-level QFrame)? We get for free:
* automatic positioning under the trigger button,
* click-outside-to-close,
* Esc-to-close,
* native dropdown shadow.
The downside — Qt closes a QMenu on any QAction trigger — is sidestepped
by using QWidgetAction everywhere: the inner widgets emit Qt signals
directly without going through QAction's "triggered" path, so the menu
stays open across multiple checkbox toggles. The user explicitly closes
via the footer button or by clicking outside.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from img_player.sequence.channels import ChannelGroup, ChannelSelection
from img_player.ui.theme import G, S


# Layout-mode tokens. Stored in QSettings exactly as listed here, so
# external code (Preferences round-trip) can compare with a string
# literal rather than importing this module.
LAYOUT_MODES: tuple[str, ...] = ("Auto", "1×N", "N×1", "2×2", "3×3", "4×4")
DEFAULT_LAYOUT_MODE = "Auto"


class _ChannelRow(QFrame):  # type: ignore[misc]
    """One line in the menu: radio (active) + label + checkbox (in tiles).

    Two independent signals — the menu wires both to the same builder
    so toggling the checkbox while a different row is active still
    emits a coherent :class:`ChannelSelection`.
    """

    radio_picked = Signal(str)        # label of the row whose radio became checked
    check_toggled = Signal(str, bool)  # (label, new_checked_state)

    def __init__(self, group: ChannelGroup, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label = group.label
        self._group = group

        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(S.SM, 2, S.SM, 2)
        layout.setSpacing(S.SM)

        self._radio = QRadioButton()
        # Radio buttons inside a QMenu need autoExclusive disabled;
        # the parent menu manages exclusivity via a single QButtonGroup
        # (otherwise two rows in the same menu fight each other when
        # a third becomes parent). The menu adds us to its group on
        # construction.
        self._radio.setAutoExclusive(False)
        self._radio.toggled.connect(self._on_radio_toggled)
        layout.addWidget(self._radio)

        self._label = QLabel(group.label)
        self._label.setMinimumWidth(120)
        self._label.setToolTip(
            "Channels: " + ", ".join(group.channels)
        )
        # Click-on-label is a synonym for click-on-radio: bigger
        # target, more forgiving UX, especially on touchpads.
        self._label.mousePressEvent = self._on_label_clicked  # type: ignore[method-assign]
        layout.addWidget(self._label, 1)

        self._check = QCheckBox()
        self._check.setToolTip("Show this channel as a tile in the contact sheet")
        self._check.toggled.connect(self._on_check_toggled)
        layout.addWidget(self._check)

    # -------------------------------------------------- public API

    def set_active(self, on: bool) -> None:
        """Set the radio without retriggering the signal."""
        self._radio.blockSignals(True)
        self._radio.setChecked(on)
        self._radio.blockSignals(False)

    def set_checked(self, on: bool) -> None:
        """Set the checkbox without retriggering the signal."""
        self._check.blockSignals(True)
        self._check.setChecked(on)
        self._check.blockSignals(False)

    @property
    def radio(self) -> QRadioButton:
        return self._radio

    # -------------------------------------------------- handlers

    def _on_radio_toggled(self, checked: bool) -> None:
        if checked:
            self.radio_picked.emit(self.label)

    def _on_check_toggled(self, checked: bool) -> None:
        self.check_toggled.emit(self.label, bool(checked))

    def _on_label_clicked(self, event) -> None:  # type: ignore[no-untyped-def]
        # Mirror a click on the radio. Don't toggle the checkbox: the
        # user has a dedicated target for that on the right.
        if event.button() == Qt.MouseButton.LeftButton:
            self._radio.setChecked(True)
        super().mousePressEvent(event)


class ChannelMenu(QMenu):  # type: ignore[misc]
    """Popup menu with one row per channel group + footer controls."""

    # Emitted whenever the active radio or any checkbox changes. The
    # carried ChannelSelection is the up-to-date state of the menu.
    selection_changed = Signal(object)
    # Emitted when the user picks a different layout mode in the
    # footer combo. Carries the chosen string token (see LAYOUT_MODES).
    layout_mode_changed = Signal(str)
    # Emitted when the "Show labels" checkbox in the footer is
    # toggled. Carries the new visibility state (True = labels
    # baked onto each tile, False = clean composite).
    labels_visible_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups: tuple[ChannelGroup, ...] = ()
        self._active_label: str = ""
        self._tile_labels: set[str] = set()
        self._layout_mode: str = DEFAULT_LAYOUT_MODE
        # Persistable on/off for the per-tile name chip. Default is
        # ON — first-run discoverability ("which tile is which?")
        # outweighs the small visual noise.
        self._labels_visible: bool = True
        # Maps label → row widget so we can drive radio/checkbox state
        # from set_groups / set_state without index juggling.
        self._row_by_label: dict[str, _ChannelRow] = {}
        # All rows share one QButtonGroup so the radios are mutually
        # exclusive across the whole menu — we did set autoExclusive
        # to False on each radio, this group reinstates it globally.
        self._radio_group = QButtonGroup(self)
        self._radio_group.setExclusive(True)

        # Ensure the menu is wide enough that long layer names don't
        # get truncated. 220 is a tested sweet spot — fits "albedo +
        # checkbox + radio" comfortably without dwarfing the transport
        # bar in the rare case of just RGB.
        self.setMinimumWidth(220)

        # The footer is rebuilt every time the rows are rebuilt so its
        # actions aren't separated from the rows by stale entries.
        self._footer_widget: _ChannelMenuFooter | None = None

    # ------------------------------------------------------------------ Public API

    def set_groups(self, groups: Iterable[ChannelGroup]) -> None:
        """Rebuild the row list from a fresh list of groups.

        Called by the transport bar when a new sequence is loaded.
        Resets state to "first group active, no tiles" — same baseline
        the legacy combo had at index 0.
        """
        self.clear()
        self._row_by_label.clear()
        # Drain the QButtonGroup — addButton() is safe to re-call but
        # the group tracks references so leaving stale ones leaks.
        for btn in list(self._radio_group.buttons()):
            self._radio_group.removeButton(btn)

        self._groups = tuple(groups)
        for group in self._groups:
            row = _ChannelRow(group, parent=self)
            row.radio_picked.connect(self._on_radio_picked)
            row.check_toggled.connect(self._on_check_toggled)
            self._radio_group.addButton(row.radio)
            action = QWidgetAction(self)
            action.setDefaultWidget(row)
            self.addAction(action)
            self._row_by_label[group.label] = row

        if self._groups:
            self._active_label = self._groups[0].label
            self._row_by_label[self._active_label].set_active(True)
        else:
            self._active_label = ""
        self._tile_labels.clear()

        self._build_footer()

    def set_state(
        self,
        active: str,
        tiles: tuple[str, ...],
        layout_mode: str = DEFAULT_LAYOUT_MODE,
        labels_visible: bool | None = None,
    ) -> None:
        """Restore a saved state without emitting selection_changed.

        Called from preferences round-trip on app boot. Unknown labels
        are silently dropped — the user might have switched sequences
        and the persisted set no longer applies.

        ``labels_visible`` is optional so old call sites that only
        manage the active/tiles/layout triplet keep compiling. ``None``
        leaves the current value untouched.
        """
        if active and active in self._row_by_label:
            self._set_active_silent(active)
        valid_tiles = tuple(t for t in tiles if t in self._row_by_label)
        for label, row in self._row_by_label.items():
            row.set_checked(label in valid_tiles)
        self._tile_labels = set(valid_tiles)
        if layout_mode in LAYOUT_MODES:
            self._layout_mode = layout_mode
            if self._footer_widget is not None:
                self._footer_widget.set_layout_mode(layout_mode)
        if labels_visible is not None:
            self._labels_visible = bool(labels_visible)
            if self._footer_widget is not None:
                self._footer_widget.set_labels_visible(self._labels_visible)

    def current_selection(self) -> ChannelSelection | None:
        """Read the current state out as a :class:`ChannelSelection`.

        ``None`` when no groups have been loaded yet (typically before
        the first sequence opens).
        """
        if not self._groups or not self._active_label:
            return None
        active_group = next(
            (g for g in self._groups if g.label == self._active_label),
            self._groups[0],
        )
        # Preserve the original group ordering for the tile tuple so
        # the contact sheet's auto layout is stable across runs.
        tile_groups = tuple(
            g for g in self._groups if g.label in self._tile_labels
        )
        return ChannelSelection(active=active_group, tiles=tile_groups)

    @property
    def layout_mode(self) -> str:
        return self._layout_mode

    @property
    def labels_visible(self) -> bool:
        return self._labels_visible

    @property
    def tile_labels(self) -> tuple[str, ...]:
        # Stable ordering for persistence: follow the group order.
        return tuple(g.label for g in self._groups if g.label in self._tile_labels)

    @property
    def active_label(self) -> str:
        return self._active_label

    # ------------------------------------------------------------------ Internals

    def _set_active_silent(self, label: str) -> None:
        """Update the active radio without triggering selection_changed."""
        if label not in self._row_by_label:
            return
        for lbl, row in self._row_by_label.items():
            row.set_active(lbl == label)
        self._active_label = label

    def _build_footer(self) -> None:
        """Add the separator + footer (layout combo + labels toggle + Reset + Close)."""
        self.addSeparator()
        footer = _ChannelMenuFooter(
            self._layout_mode, self._labels_visible, parent=self,
        )
        footer.layout_picked.connect(self._on_layout_picked)
        footer.labels_toggled.connect(self._on_labels_toggled)
        footer.reset_clicked.connect(self._on_reset_clicked)
        footer.close_clicked.connect(self.hide)
        action = QWidgetAction(self)
        action.setDefaultWidget(footer)
        self.addAction(action)
        self._footer_widget = footer

    def _emit_selection(self) -> None:
        sel = self.current_selection()
        if sel is not None:
            self.selection_changed.emit(sel)

    def _on_radio_picked(self, label: str) -> None:
        self._active_label = label
        self._emit_selection()

    def _on_check_toggled(self, label: str, checked: bool) -> None:
        if checked:
            self._tile_labels.add(label)
        else:
            self._tile_labels.discard(label)
        self._emit_selection()

    def _on_layout_picked(self, mode: str) -> None:
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        self.layout_mode_changed.emit(mode)

    def _on_labels_toggled(self, on: bool) -> None:
        if on == self._labels_visible:
            return
        self._labels_visible = bool(on)
        self.labels_visible_changed.emit(self._labels_visible)

    def _on_reset_clicked(self) -> None:
        """Uncheck every tile checkbox; the active radio stays where
        it is so the user is brought back to single-display mode on
        their currently-selected channel rather than reset to RGB.
        """
        if not self._tile_labels:
            return
        for label in list(self._tile_labels):
            self._row_by_label[label].set_checked(False)
        self._tile_labels.clear()
        self._emit_selection()


class _ChannelMenuFooter(QFrame):  # type: ignore[misc]
    """Footer of the channel menu.

    Two stacked rows — the menu was getting crowded otherwise:

        Layout: [Auto ▼]   ☐ Show labels
        [ Reset all ]      [ Close ]

    Lives in its own QFrame so the QMenu's separator above sits at the
    right place visually. Four signals — wired by the parent menu.
    """

    layout_picked = Signal(str)
    labels_toggled = Signal(bool)
    reset_clicked = Signal()
    close_clicked = Signal()

    def __init__(
        self,
        current_mode: str,
        labels_visible: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(S.SM, S.SM, S.SM, S.SM)
        outer.setSpacing(S.SM)

        # Row 1: Layout combo + Show labels checkbox.
        row1 = QHBoxLayout()
        row1.setSpacing(S.SM)
        row1.addWidget(QLabel("Layout"))
        self._layout_combo = QComboBox()
        self._layout_combo.setFixedHeight(G.INPUT_H)
        self._layout_combo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        for mode in LAYOUT_MODES:
            self._layout_combo.addItem(mode)
        if current_mode in LAYOUT_MODES:
            self._layout_combo.setCurrentText(current_mode)
        # ``activated`` (not ``currentIndexChanged``) — only fires on
        # user clicks, avoiding loops when set_layout_mode() updates
        # the combo from outside.
        self._layout_combo.activated.connect(self._on_layout_activated)
        row1.addWidget(self._layout_combo)
        row1.addStretch(1)
        self._labels_check = QCheckBox("Show labels")
        self._labels_check.setToolTip(
            "Toggle the per-tile name chip (visible only in contact-sheet mode)"
        )
        self._labels_check.setChecked(bool(labels_visible))
        self._labels_check.toggled.connect(self.labels_toggled.emit)
        row1.addWidget(self._labels_check)
        outer.addLayout(row1)

        # Row 2: Reset + Close.
        row2 = QHBoxLayout()
        row2.setSpacing(S.SM)
        self._reset_btn = QPushButton("Reset all")
        self._reset_btn.setFixedHeight(G.INPUT_H)
        self._reset_btn.setToolTip(
            "Uncheck every tile (return to single-channel display)"
        )
        self._reset_btn.clicked.connect(self.reset_clicked.emit)
        row2.addWidget(self._reset_btn)
        row2.addStretch(1)
        self._close_btn = QPushButton("Close")
        self._close_btn.setFixedHeight(G.INPUT_H)
        self._close_btn.clicked.connect(self.close_clicked.emit)
        row2.addWidget(self._close_btn)
        outer.addLayout(row2)

    def set_layout_mode(self, mode: str) -> None:
        if mode in LAYOUT_MODES:
            self._layout_combo.blockSignals(True)
            self._layout_combo.setCurrentText(mode)
            self._layout_combo.blockSignals(False)

    def set_labels_visible(self, on: bool) -> None:
        if self._labels_check.isChecked() == bool(on):
            return
        self._labels_check.blockSignals(True)
        self._labels_check.setChecked(bool(on))
        self._labels_check.blockSignals(False)

    def _on_layout_activated(self, index: int) -> None:
        text = self._layout_combo.itemText(index)
        self.layout_picked.emit(text)

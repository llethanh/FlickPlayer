"""Smoke tests for :class:`LayerPanel` — drawing, signals, collapse.

The widget is mostly cosmetic but the LayerStack signal wiring is
load-bearing for the multi-layer feature, so we pin its main
behaviours.
"""

from __future__ import annotations

from pathlib import Path

from img_player.layers import Layer, LayerStack
from img_player.sequence.models import FrameInfo, SequenceInfo
from img_player.ui.layer_panel import LayerPanel


def _seq(first: int = 1001, last: int = 1100) -> SequenceInfo:
    frames = tuple(
        FrameInfo(path=Path(f"/fake/{n}.exr"), frame_number=n)
        for n in range(first, last + 1)
    )
    return SequenceInfo(
        base_name="x", extension=".exr", directory=Path("/fake"),
        padding=4, frames=frames,
    )


def _layer(offset: int = 0, name: str = "shotA") -> Layer:
    return Layer.from_sequence(_seq(), offset=offset, name=name)


# ============================================================================
# Construction + rebuild
# ============================================================================


class TestConstruction:
    def test_empty_stack_shows_hint(self, qtbot) -> None:
        stack = LayerStack()
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        # Header visible, no rows. The empty hint is a QLabel inside
        # the rows host — we don't assert its text, just that no
        # crash occurred and the count reads "0".
        assert panel._count_label.text() == "0"  # type: ignore[attr-defined]

    def test_single_layer_renders_row(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        stack.add(a)
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        assert panel._count_label.text() == "1"  # type: ignore[attr-defined]
        assert a.id in panel._rows  # type: ignore[attr-defined]

    def test_layers_added_after_construction_appear(self, qtbot) -> None:
        stack = LayerStack()
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        b = _layer(name="B")
        stack.add(b)
        assert b.id in panel._rows  # type: ignore[attr-defined]
        assert panel._count_label.text() == "1"  # type: ignore[attr-defined]


# ============================================================================
# Collapse
# ============================================================================


class TestCollapse:
    def test_default_expanded(self, qtbot) -> None:
        stack = LayerStack()
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        assert panel.is_collapsed() is False

    def test_set_collapsed(self, qtbot) -> None:
        stack = LayerStack()
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        panel.set_collapsed(True)
        assert panel.is_collapsed() is True
        panel.set_collapsed(False)
        assert panel.is_collapsed() is False

    def test_set_collapsed_idempotent(self, qtbot) -> None:
        stack = LayerStack()
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        # Calling with the same value should be a no-op (no exception).
        panel.set_collapsed(False)
        panel.set_collapsed(False)


# ============================================================================
# Visibility toggle round-trip
# ============================================================================


class TestVisibility:
    def test_panel_eye_click_toggles_layer(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        row = panel._rows[a.id]  # type: ignore[attr-defined]
        # Click the eye button — the row emits, the panel forwards
        # to LayerStack.toggle_visible.
        assert stack.find(a.id).visible is True
        row._eye_btn.click()  # type: ignore[attr-defined]
        assert stack.find(a.id).visible is False

    def test_external_visibility_change_updates_row(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        row = panel._rows[a.id]  # type: ignore[attr-defined]
        stack.set_visible(a.id, False)
        # Eye button must reflect the new state.
        assert row._eye_btn.isChecked() is False  # type: ignore[attr-defined]


# ============================================================================
# Reorder
# ============================================================================
#
# The ``↑`` / ``↓`` buttons that used to live on each row were
# replaced by drag-and-drop reordering (see
# :data:`~img_player.ui.layer_panel._REORDER_MIME` and the
# ``reorder_drag_*`` signals on :class:`LayerRow`). The model-level
# reorder semantics (``LayerStack.reorder``) are still exhaustively
# covered by :mod:`tests.unit.test_layer_stack`. The UI-level
# drag-and-drop is intentionally not unit-tested here because Qt's
# drag machinery requires a real event-loop spin to deliver
# ``QDragMoveEvent`` / ``dropEvent`` reliably — those tests live in
# ``tests/integration`` if/when we add them.


# ============================================================================
# Focus
# ============================================================================


class TestFocus:
    def test_first_layer_auto_focused(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        assert panel._rows[a.id]._focused is True  # type: ignore[attr-defined]

    def test_external_focus_change_updates_rows(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        b = _layer(name="B")
        stack.add(a)
        stack.add(b, position=999)  # bottom
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        stack.set_focus(b.id)
        assert panel._rows[b.id]._focused is True  # type: ignore[attr-defined]
        assert panel._rows[a.id]._focused is False  # type: ignore[attr-defined]

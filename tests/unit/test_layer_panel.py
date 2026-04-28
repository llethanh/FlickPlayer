"""Smoke tests for :class:`LayerPanel` — drawing, signals, collapse.

The widget is mostly cosmetic but the LayerStack signal wiring is
load-bearing for the multi-layer feature, so we pin its main
behaviours.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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
# Reorder buttons
# ============================================================================


class TestReorderButtons:
    def test_move_up(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        b = _layer(name="B")
        stack.add(a, position=0)  # top
        stack.add(b, position=1)  # bottom
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        # Click "↑" on B (currently at index 1) → it goes to 0.
        panel._rows[b.id]._up_btn.click()  # type: ignore[attr-defined]
        assert [layer.name for layer in stack] == ["B", "A"]

    def test_move_down(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        b = _layer(name="B")
        stack.add(a, position=0)
        stack.add(b, position=1)
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        panel._rows[a.id]._down_btn.click()  # type: ignore[attr-defined]
        assert [layer.name for layer in stack] == ["B", "A"]

    def test_topmost_cant_move_up(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        b = _layer(name="B")
        stack.add(a, position=0)
        stack.add(b, position=1)
        panel = LayerPanel(stack)
        qtbot.addWidget(panel)
        # The top layer's ↑ should be disabled.
        assert panel._rows[a.id]._up_btn.isEnabled() is False  # type: ignore[attr-defined]
        # The bottom layer's ↓ should also be disabled.
        assert panel._rows[b.id]._down_btn.isEnabled() is False  # type: ignore[attr-defined]


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

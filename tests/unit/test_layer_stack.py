"""Tests for :class:`LayerStack` — composition + topmost-visible resolution.

Uses qtbot for signal-emission assertions but doesn't touch GL or
the cache. Pure logic plus Qt signal plumbing.
"""

from __future__ import annotations

from pathlib import Path

from img_player.layers import Layer, LayerStack
from img_player.sequence.models import FrameInfo, SequenceInfo


def _seq(first: int = 1001, last: int = 1100) -> SequenceInfo:
    frames = tuple(
        FrameInfo(path=Path(f"/fake/{n}.exr"), frame_number=n)
        for n in range(first, last + 1)
    )
    return SequenceInfo(
        base_name="x", extension=".exr", directory=Path("/fake"),
        padding=4, frames=frames,
    )


def _layer(first: int = 1001, last: int = 1100, offset: int = 0,
           name: str = "") -> Layer:
    return Layer.from_sequence(_seq(first, last), offset=offset, name=name)


# ============================================================================
# Composition (add / remove / reorder)
# ============================================================================


class TestComposition:
    def test_empty_stack(self, qtbot) -> None:
        stack = LayerStack()
        qtbot.addWidget(stack) if False else None
        assert len(stack) == 0
        assert not stack
        assert stack.layers() == ()
        assert stack.master_range() == (0, 0)
        assert stack.master_length() == 0

    def test_add_at_top_by_default(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        b = _layer(name="B")
        stack.add(a)
        stack.add(b)
        # B was added second with default position 0 (top), so order
        # is [B, A] from top.
        assert [layer.name for layer in stack] == ["B", "A"]

    def test_add_at_specific_position(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        b = _layer(name="B")
        c = _layer(name="C")
        stack.add(a, position=0)
        stack.add(b, position=0)  # B goes to top
        stack.add(c, position=1)  # C between B and A
        assert [layer.name for layer in stack] == ["B", "C", "A"]

    def test_add_clamps_out_of_range_position(self, qtbot) -> None:
        stack = LayerStack()
        stack.add(_layer(name="A"))
        # Position 999 → clamps to len(stack) = 1 → bottom.
        stack.add(_layer(name="B"), position=999)
        assert [layer.name for layer in stack] == ["A", "B"]

    def test_add_emits_layers_changed(self, qtbot) -> None:
        stack = LayerStack()
        with qtbot.waitSignal(stack.layers_changed, timeout=200):
            stack.add(_layer())

    def test_remove_known_layer(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        b = _layer(name="B")
        stack.add(a)
        stack.add(b)
        stack.remove(a.id)
        assert [layer.name for layer in stack] == ["B"]

    def test_remove_unknown_id_is_noop(self, qtbot) -> None:
        stack = LayerStack()
        stack.add(_layer())
        stack.remove("does-not-exist")
        assert len(stack) == 1

    def test_reorder_to_top(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        b = _layer(name="B")
        c = _layer(name="C")
        for layer in (a, b, c):
            stack.add(layer, position=999)  # bottom each time
        assert [l.name for l in stack] == ["A", "B", "C"]
        stack.reorder(c.id, 0)
        assert [l.name for l in stack] == ["C", "A", "B"]

    def test_reorder_idempotent(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(name="A")
        stack.add(a)
        # Reordering a layer to the position it already occupies
        # must not emit a signal.
        with qtbot.assertNotEmitted(stack.layers_changed):
            stack.reorder(a.id, 0)


# ============================================================================
# Visibility
# ============================================================================


class TestVisibility:
    def test_default_visible(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        assert stack.find(a.id).visible is True

    def test_toggle_flips(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        stack.toggle_visible(a.id)
        assert stack.find(a.id).visible is False
        stack.toggle_visible(a.id)
        assert stack.find(a.id).visible is True

    def test_toggle_emits_visibility_changed_with_id(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        with qtbot.waitSignal(stack.visibility_changed, timeout=200) as sig:
            stack.toggle_visible(a.id)
        assert sig.args == [a.id]

    def test_set_visible_idempotent(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        with qtbot.assertNotEmitted(stack.visibility_changed):
            stack.set_visible(a.id, True)  # already true


# ============================================================================
# Focus
# ============================================================================


class TestFocus:
    def test_first_add_auto_focuses(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        assert stack.focused_id == a.id
        assert stack.focused() is a

    def test_subsequent_add_does_not_steal_focus(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        b = _layer()
        stack.add(a)
        stack.add(b)
        assert stack.focused_id == a.id  # first one stays

    def test_set_focus_emits(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        b = _layer()
        stack.add(a)
        stack.add(b)
        with qtbot.waitSignal(stack.focus_changed, timeout=200) as sig:
            stack.set_focus(b.id)
        assert sig.args == [b.id]

    def test_remove_focused_shifts_focus(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        b = _layer()
        stack.add(a)
        stack.add(b, position=999)  # B at bottom
        # A is focused (first). Remove A → focus goes to B (now top).
        stack.remove(a.id)
        assert stack.focused_id == b.id

    def test_remove_last_clears_focus(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        stack.remove(a.id)
        assert stack.focused_id == ""


# ============================================================================
# Topmost-visible resolution + master range
# ============================================================================


class TestTopmostVisible:
    def test_no_layers_returns_none(self, qtbot) -> None:
        stack = LayerStack()
        assert stack.topmost_visible_at(0) is None

    def test_single_layer_visible(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(offset=0)
        stack.add(a)
        # offset 0, length 100 → covers 0..99
        assert stack.topmost_visible_at(50) is a
        assert stack.topmost_visible_at(99) is a
        assert stack.topmost_visible_at(100) is None  # past the end

    def test_topmost_wins_when_overlap(self, qtbot) -> None:
        stack = LayerStack()
        bottom = _layer(offset=0, name="bottom")
        top = _layer(offset=50, name="top")
        # Add bottom first then top → top ends up at index 0 (top).
        stack.add(bottom)
        stack.add(top)
        # At master 0, only `bottom` covers.
        assert stack.topmost_visible_at(0) is bottom
        # At master 50, both cover. Top of stack wins.
        assert stack.topmost_visible_at(50) is top

    def test_hidden_topmost_falls_through(self, qtbot) -> None:
        stack = LayerStack()
        bottom = _layer(offset=0, name="bottom")
        top = _layer(offset=50, name="top")
        stack.add(bottom)
        stack.add(top)
        stack.set_visible(top.id, False)
        # Top is hidden — bottom shows through.
        assert stack.topmost_visible_at(60) is bottom

    def test_all_hidden_returns_none(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(offset=0)
        stack.add(a)
        stack.set_visible(a.id, False)
        assert stack.topmost_visible_at(50) is None  # → écran noir

    def test_gap_between_layers_returns_none(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer(offset=0, name="A")  # 0..99
        b = _layer(offset=200, name="B")  # 200..299
        stack.add(a)
        stack.add(b)
        # Master 150 is in the gap → no layer covers.
        assert stack.topmost_visible_at(150) is None


class TestMasterRange:
    def test_single_layer(self, qtbot) -> None:
        stack = LayerStack()
        stack.add(_layer(offset=10))  # master 10..109
        assert stack.master_range() == (10, 109)
        assert stack.master_length() == 100

    def test_union_of_two_layers(self, qtbot) -> None:
        stack = LayerStack()
        stack.add(_layer(offset=0))    # 0..99
        stack.add(_layer(offset=200))  # 200..299
        # Union = (0, 299), length = 300 (gap counts toward length).
        assert stack.master_range() == (0, 299)
        assert stack.master_length() == 300

    def test_negative_offset_extends_left(self, qtbot) -> None:
        stack = LayerStack()
        stack.add(_layer(offset=-50))  # -50..49
        stack.add(_layer(offset=0))    # 0..99
        assert stack.master_range() == (-50, 99)
        assert stack.master_length() == 150


# ============================================================================
# Update — bulk per-layer state mutation
# ============================================================================


class TestUpdate:
    def test_updates_known_field(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        stack.update(a.id, exposure=2.0, gamma=1.8)
        assert stack.find(a.id).exposure == 2.0
        assert stack.find(a.id).gamma == 1.8

    def test_emits_single_modified_signal(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        with qtbot.waitSignal(stack.layer_modified, timeout=200) as sig:
            stack.update(a.id, exposure=1.0, gamma=2.2)
        assert sig.args == [a.id]

    def test_unknown_field_silently_ignored(self, qtbot) -> None:
        stack = LayerStack()
        a = _layer()
        stack.add(a)
        # Should not raise; the unknown attribute is logged.
        stack.update(a.id, bogus_field=42)

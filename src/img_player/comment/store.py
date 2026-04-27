"""The :class:`CommentStore` — per-frame ordered list of comments.

Mirrors :class:`~img_player.annotate.store.AnnotationStore` in
shape: a `QObject` keyed by frame index, with Qt signals for the
UI to react. Adds the CRUD operations for individual comments
(add / edit / delete) since unlike strokes, comments stay user-
addressable after creation — the panel needs a stable handle to
edit or remove a specific row.

Design notes:

* No undo stack. Comments are textual, edits are deliberate, and
  Slack / Kitsu / etc. don't ship undo for comment edits either.
  Worst case the user re-types — much simpler model.
* Dirty tracking, same contract as ``AnnotationStore``: any
  mutation flips ``_dirty``, ``load_from_dict`` clears it.
* The shared sidecar (with annotations) is loaded / saved by the
  app at sequence open / close — see
  :mod:`img_player.comment.persistence`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from img_player.comment.comment import Comment

log = logging.getLogger(__name__)


@dataclass
class _FrameState:
    """Per-frame container. Internal — do not import."""

    comments: list[Comment] = field(default_factory=list)


class CommentStore(QObject):
    """Per-frame textual comments. Qt signals for the UI."""

    commented_frames_changed = Signal()
    """Emitted when the set ``{f : len(comments_at(f)) > 0}`` mutates.

    Consumers: the timeline (markers, combined with annotated
    frames into "frames with notes") and the transport bar (future
    prev/next-noted buttons).
    """

    frame_comments_changed = Signal(int)
    """Emitted when a specific frame's comment list mutates.

    Consumers: the comment panel (re-renders the list when its
    current frame changes).
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._frames: dict[int, _FrameState] = {}
        self._dirty: bool = False

    # ------------------------------------------------------------------ Read

    def comments_at(self, frame: int) -> tuple[Comment, ...]:
        """All comments on ``frame`` in chronological (insertion) order."""
        state = self._frames.get(frame)
        return tuple(state.comments) if state is not None else ()

    def commented_frames(self) -> frozenset[int]:
        """Set of frame indices that carry at least one comment."""
        return frozenset(f for f, s in self._frames.items() if s.comments)

    def is_dirty(self) -> bool:
        """``True`` if any mutation happened since the last
        :meth:`load_from_dict` or :meth:`mark_clean`."""
        return self._dirty

    def mark_clean(self) -> None:
        """Reset the dirty flag — call after successful save."""
        self._dirty = False

    # ------------------------------------------------------------------ Mutate

    def add_comment(self, frame: int, text: str) -> Comment:
        """Append a fresh comment to ``frame``. Returns the new
        :class:`Comment` so the caller can dismiss the edit field
        knowing the id."""
        text = text.strip()
        if not text:
            raise ValueError("Comment text cannot be empty")
        state = self._frames.setdefault(frame, _FrameState())
        was_empty = not state.comments
        comment = Comment.new(text)
        state.comments.append(comment)
        self._dirty = True
        self.frame_comments_changed.emit(frame)
        if was_empty:
            self.commented_frames_changed.emit()
        return comment

    def edit_comment(self, frame: int, comment_id: str, new_text: str) -> bool:
        """Replace the text of the comment with the given id. Returns
        ``True`` on success, ``False`` if the id wasn't found.

        Raises ``ValueError`` for an empty new text — same contract
        as :meth:`add_comment`. The store does not silently delete
        a comment via an empty edit; the user must use
        :meth:`delete_comment` explicitly.
        """
        new_text = new_text.strip()
        if not new_text:
            raise ValueError("Comment text cannot be empty")
        state = self._frames.get(frame)
        if state is None:
            return False
        for i, existing in enumerate(state.comments):
            if existing.id == comment_id:
                state.comments[i] = existing.edited(new_text)
                self._dirty = True
                self.frame_comments_changed.emit(frame)
                return True
        return False

    def delete_comment(self, frame: int, comment_id: str) -> bool:
        """Remove the comment with the given id. Returns ``True`` on
        success, ``False`` if the id wasn't found.

        Unlike :meth:`AnnotationStore.remove_stroke`, deletes are
        **not undoable** — comment text is small and re-typing is
        cheap; carrying an undo stack just for comments would
        complicate the model with little user benefit.
        """
        state = self._frames.get(frame)
        if state is None:
            return False
        for i, existing in enumerate(state.comments):
            if existing.id == comment_id:
                del state.comments[i]
                self._dirty = True
                self.frame_comments_changed.emit(frame)
                if not state.comments:
                    self.commented_frames_changed.emit()
                return True
        return False

    # ------------------------------------------------------------------ Persistence

    def to_dict(self) -> dict[str, list[dict[str, str]]]:
        """Serialise the comments tree (without the dirty flag).

        Shape: ``{<frame_str>: [<comment dict>, ...]}``. The
        persistence module wraps this under
        ``sequences[<basename>]["comments"]`` in the on-disk JSON.
        """
        out: dict[str, list[dict[str, str]]] = {}
        for frame, state in self._frames.items():
            if state.comments:
                out[str(frame)] = [c.to_dict() for c in state.comments]
        return out

    def load_from_dict(self, data: dict[str, list[dict[str, object]]]) -> None:
        """Replace state from the JSON dict. Skips malformed rows."""
        self._frames.clear()
        for frame_str, comment_dicts in data.items():
            try:
                frame = int(frame_str)
            except (TypeError, ValueError):
                continue
            kept: list[Comment] = []
            for cd in comment_dicts:
                try:
                    kept.append(Comment.from_dict(cd))
                except (KeyError, TypeError, ValueError):
                    log.warning(
                        "[comment] dropping malformed comment on frame %d",
                        frame,
                    )
                    continue
            if kept:
                self._frames[frame] = _FrameState(comments=kept)
        # Loading is the "now matches disk" moment; reset dirty.
        self._dirty = False
        self.commented_frames_changed.emit()

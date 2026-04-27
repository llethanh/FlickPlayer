"""The :class:`CommentPanel` — review-tool comment thread for a frame.

Lives in the right-hand side dock as the "Comments" tab. Mirrors the
review experience of AYON / Kitsu / ShotGrid:

* Header line: ``"Comments — Frame N (K)"`` keeps the user oriented.
* Scrollable list of cards, oldest-to-newest (chat-log order). Each
  card shows: author + local timestamp on top, the comment text, an
  optional ``(modifié à HH:MM)`` tag when the comment has been
  edited, and edit / delete buttons.
* Bottom: a multi-line input + an "Add" button. The input is
  cleared after a successful add.

The panel doesn't own the data — it reads from the
:class:`~img_player.comment.store.CommentStore` and writes through
the store's CRUD methods. Re-renders when ``frame_comments_changed``
fires for the current frame.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from img_player.comment.comment import Comment
from img_player.comment.store import CommentStore
from img_player.ui.theme import S

log = logging.getLogger(__name__)


# -------------------------------------------------------- Date formatting

_MONTHS_FR = (
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
)


def _format_local(iso: str) -> str:
    """Convert a stored ISO-UTC timestamp to a short local string.

    ``"2026-04-27T18:42:37+00:00"`` → ``"27 avr. 18:42"`` (in the
    user's local zone). Falls back to the raw string on any parse
    error so a corrupt row doesn't crash the panel.
    """
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso
    local = dt.astimezone()
    month = _MONTHS_FR[local.month - 1]
    return f"{local.day} {month} {local.hour:02d}:{local.minute:02d}"


def _format_hhmm(iso: str) -> str:
    """``"...T19:01:14+00:00"`` → ``"19:01"``. Used for the
    ``(modifié à HH:MM)`` tag."""
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    local = dt.astimezone()
    return f"{local.hour:02d}:{local.minute:02d}"


# -------------------------------------------------------- Comment card


class _CommentCard(QFrame):
    """A single comment row inside the scroll area."""

    edit_requested = Signal(str, str)  # comment_id, current text (for the dialog default)
    delete_requested = Signal(str)     # comment_id

    def __init__(self, comment: Comment, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._comment_id = comment.id
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "_CommentCard {"
            "  background: #2C2C30;"  # BG_SURFACE
            "  border: 1px solid #38383C;"  # BORDER_DEFAULT
            "  border-radius: 6px;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # --- Header: author • date  [edit] [delete]
        header = QHBoxLayout()
        header.setSpacing(6)

        meta = QLabel(f"{comment.author} • {_format_local(comment.created_at)}")
        meta_font = QFont(meta.font())
        meta_font.setPointSize(meta_font.pointSize() - 1)
        meta.setFont(meta_font)
        meta.setStyleSheet("color: #8A8A8E;")  # TEXT_SECONDARY
        meta.setSizePolicy(meta.sizePolicy().horizontalPolicy(), meta.sizePolicy().verticalPolicy())
        header.addWidget(meta)
        header.addStretch(1)

        edit_btn = QToolButton(self)
        edit_btn.setText("✏️")
        edit_btn.setToolTip("Modifier ce commentaire")
        edit_btn.setFixedSize(22, 22)
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.clicked.connect(
            lambda: self.edit_requested.emit(self._comment_id, comment.text)
        )
        header.addWidget(edit_btn)

        delete_btn = QToolButton(self)
        delete_btn.setText("🗑")
        delete_btn.setToolTip("Supprimer ce commentaire")
        delete_btn.setFixedSize(22, 22)
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.clicked.connect(
            lambda: self.delete_requested.emit(self._comment_id)
        )
        header.addWidget(delete_btn)

        layout.addLayout(header)

        # --- Text body. Wraps. Selectable for copy.
        text_label = QLabel(comment.text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        text_label.setStyleSheet("color: #E4E4E6;")  # TEXT_PRIMARY
        layout.addWidget(text_label)

        # --- Optional "modifié à HH:MM" tag.
        if comment.is_edited:
            edited_label = QLabel(
                f"(modifié à {_format_hhmm(comment.updated_at)})"
            )
            edited_font = QFont(edited_label.font())
            edited_font.setPointSize(edited_font.pointSize() - 1)
            edited_font.setItalic(True)
            edited_label.setFont(edited_font)
            edited_label.setStyleSheet("color: #8A8A8E;")
            layout.addWidget(edited_label)


# -------------------------------------------------------- Panel


class CommentPanel(QWidget):
    """Side-dock panel displaying / editing comments for a frame."""

    def __init__(
        self,
        store: CommentStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._current_frame: int = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(S.SM, S.SM, S.SM, S.SM)
        outer.setSpacing(S.SM)

        # --- Header
        self._header = QLabel("Comments — Frame 0 (0)")
        header_font = QFont(self._header.font())
        header_font.setBold(True)
        self._header.setFont(header_font)
        outer.addWidget(self._header)

        # --- Scrollable list
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll_inner = QWidget()
        self._cards_layout = QVBoxLayout(scroll_inner)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(scroll_inner)
        outer.addWidget(self._scroll, stretch=1)

        # --- Empty-state placeholder, shown when the current frame
        # has no comments yet. Friendlier than a blank scroll area.
        self._empty_label = QLabel("Aucun commentaire sur cette frame.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: #8A8A8E; font-style: italic; padding: 16px;"
        )
        # Insert before the layout's stretch.
        self._cards_layout.insertWidget(0, self._empty_label)

        # --- Visual separator between the read area (cards above)
        # and the write area (compose new comment below). Without
        # this, the input field bleeds into the last card and the
        # user loses orientation about where to type.
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: #38383C;")  # BORDER_DEFAULT
        separator.setFixedHeight(1)
        outer.addWidget(separator)

        # --- Add input + button, wrapped in a styled container so
        # the compose section reads as its own block (matches the
        # way the cards above each have their own framed box).
        compose_box = QFrame(self)
        compose_box.setStyleSheet(
            "QFrame {"
            "  background: rgba(36, 36, 40, 180);"  # BG_RAISED-ish
            "  border-radius: 6px;"
            "}"
        )
        compose_box.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        compose_layout = QVBoxLayout(compose_box)
        compose_layout.setContentsMargins(8, 8, 8, 8)
        compose_layout.setSpacing(6)

        compose_caption = QLabel("Nouveau commentaire", compose_box)
        compose_caption_font = QFont(compose_caption.font())
        compose_caption_font.setPointSize(compose_caption_font.pointSize() - 1)
        compose_caption.setFont(compose_caption_font)
        compose_caption.setStyleSheet("color: #8A8A8E;")  # TEXT_SECONDARY
        compose_layout.addWidget(compose_caption)

        self._input = QPlainTextEdit(compose_box)
        self._input.setPlaceholderText(
            "Ajouter un commentaire sur cette frame…"
        )
        self._input.setFixedHeight(60)
        compose_layout.addWidget(self._input)

        self._add_btn = QPushButton("Ajouter", compose_box)
        self._add_btn.clicked.connect(self._on_add_clicked)
        compose_layout.addWidget(self._add_btn)

        outer.addWidget(compose_box)

        # --- Wire store
        self._store.frame_comments_changed.connect(self._on_frame_comments_changed)

        # First render.
        self._render()

    # ------------------------------------------------------------------ Public API

    def set_current_frame(self, frame: int) -> None:
        """Change which frame's comments the panel displays."""
        if frame == self._current_frame:
            return
        self._current_frame = frame
        self._render()

    # ------------------------------------------------------------------ Slots

    def _on_frame_comments_changed(self, frame: int) -> None:
        if frame == self._current_frame:
            self._render()

    def _on_add_clicked(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        try:
            self._store.add_comment(self._current_frame, text)
        except ValueError:
            return  # empty — already guarded above; defensive
        self._input.clear()

    def _on_card_edit_requested(
        self, comment_id: str, current_text: str
    ) -> None:
        new_text, ok = QInputDialog.getMultiLineText(
            self,
            "Modifier le commentaire",
            "Texte :",
            current_text,
        )
        if not ok:
            return
        new_text = new_text.strip()
        if not new_text:
            # Empty edit — interpret as cancel rather than silently
            # deleting (the user must use the trash icon explicitly).
            return
        try:
            self._store.edit_comment(self._current_frame, comment_id, new_text)
        except ValueError:
            return

    def _on_card_delete_requested(self, comment_id: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Supprimer le commentaire ?",
            "Cette action ne peut pas être annulée.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._store.delete_comment(self._current_frame, comment_id)

    # ------------------------------------------------------------------ Render

    def _render(self) -> None:
        # Clear out existing cards (everything before the trailing
        # stretch and the empty-label).
        while self._cards_layout.count() > 0:
            item = self._cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._empty_label:
                widget.deleteLater()

        comments = self._store.comments_at(self._current_frame)
        self._header.setText(
            f"Comments — Frame {self._current_frame} ({len(comments)})"
        )

        if not comments:
            self._cards_layout.addWidget(self._empty_label)
            self._empty_label.show()
            self._cards_layout.addStretch(1)
            return

        self._empty_label.hide()
        for c in comments:
            card = _CommentCard(c, self)
            card.edit_requested.connect(self._on_card_edit_requested)
            card.delete_requested.connect(self._on_card_delete_requested)
            self._cards_layout.addWidget(card)
        self._cards_layout.addStretch(1)

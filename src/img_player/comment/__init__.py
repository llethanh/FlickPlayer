"""Per-frame comments — text notes attached to specific frames.

Companion to :mod:`img_player.annotate`: where annotations are
visual (strokes drawn on the image), comments are textual ("fix the
timing here", "approved", "needs another pass on the rim light"…).
Both attach to a frame index, both persist in the same sidecar
JSON next to the sequence.

Inspired by AYON / Kitsu / ShotGrid review tools.

* :class:`Comment` — frozen dataclass with a UUID, text, author,
  created_at and updated_at timestamps. Serialises to JSON.
* :class:`CommentStore` — per-frame ordered list of comments,
  with add / edit / delete + dirty tracking + Qt signals for the
  panel to react to.
* :func:`save_comments` / :func:`load_comments` — read / write
  the ``"comments"`` sub-tree of the shared sidecar. Coexists
  with the annotation strokes' ``"frames"`` sub-tree at the
  same per-basename layer.
"""

from img_player.comment.comment import Comment
from img_player.comment.persistence import load_comments, save_comments
from img_player.comment.store import CommentStore

__all__ = [
    "Comment",
    "CommentStore",
    "load_comments",
    "save_comments",
]

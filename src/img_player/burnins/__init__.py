"""Burnins — info bars composited onto the viewer's RGBA output.

A *burnin* is a thin horizontal strip drawn at the top and/or bottom
of an image with review metadata: sequence name, frame number, user,
date, a studio logo, etc. The strip is composited into the pixels
themselves (not a Qt overlay) so it survives the contact-sheet path
AND the export pipeline.

Architecture in three layers:

1. **Model** (:mod:`img_player.burnins.model`) — frozen dataclasses
   describing a template (bars × elements), JSON load/save, the
   3 shipped builtins. Pure logic, no dependencies on Qt or Pillow.
2. **Tokens** (:mod:`img_player.burnins.tokens`) — substitute
   ``{frame}``, ``{layer_name}``, ``{timecode}``, etc. against a
   per-render context. Pure logic.
3. **Renderer** (:mod:`img_player.burnins.renderer`) — turn a
   template + context into pixels, using Pillow for text and image
   rasterisation. Returns the composited RGBA array; the GL upload
   path and the contact-sheet composer feed it.

Templates live as ``*.burnin.json`` files under
``%APPDATA%/FlickPlayer/burnins/`` (per-user) — JSON keeps the
storage consistent with sessions + the annotation sidecar, and
supports the nested array-of-tables shape natively.
"""

from img_player.burnins.model import (
    BurninBar,
    BurninTemplate,
    ImageElement,
    SpacerElement,
    TextElement,
    load_template,
    save_template,
)

__all__ = [
    "BurninBar",
    "BurninTemplate",
    "ImageElement",
    "SpacerElement",
    "TextElement",
    "load_template",
    "save_template",
]

"""Multi-sequence layer-stack model + master-timeline resolution.

Public API for the v1.0 multi-layer feature. The :class:`Layer`
dataclass holds one sequence + its position/state on the master
timeline. The :class:`LayerStack` QObject owns the ordered list +
emits signals on every mutation so the UI and cache can react.

The order convention is **index 0 = top of stack = highest priority**
(Photoshop / Nuke / PDPlayer). When two visible layers cover the
same master frame, the one with the lower index wins.

The package is deliberately Qt-light at the model layer (Layer is a
plain dataclass) and pulls Qt only in :class:`LayerStack` for
signal emission. That lets pure-numeric tests run without an event
loop.
"""

from __future__ import annotations

from img_player.layers.models import Layer
from img_player.layers.stack import LayerStack

__all__ = ["Layer", "LayerStack"]

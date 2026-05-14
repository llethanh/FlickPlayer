"""Tiny pure-Python coercion helpers shared by :mod:`preferences` and
:mod:`export.settings`.

Both modules need to round-trip values that were stored via
``QSettings.value()`` (which returns platform-dependent types — int on
Windows registry, string on POSIX .conf files), but ``export.settings``
is contractually pure (no Qt import) so we can't share the helper from
:mod:`preferences` directly. Hence this tiny module that neither side
depends on anything beyond stdlib types.

Kept private (``_value_coerce``) because the surface is intentionally
narrow — add new coercers here when a second consumer appears, not when
the first one needs them.
"""

from __future__ import annotations


def qsettings_bool(raw: object, default: bool = False) -> bool:
    """Coerce a ``QSettings.value()`` return into a :class:`bool`.

    QSettings on Windows returns ``int`` 0/1 from REG_DWORD-stored
    values, while on macOS/Linux .conf files round-trip booleans as
    the strings ``"true"`` / ``"false"`` (or sometimes ``"0"``/``"1"``).
    This helper folds both into a real Python bool, with ``default``
    taking over when:

    * the key is missing (``raw is None``),
    * the value is an unrecognised string (e.g. ``"maybe"``) — a naive
      ``bool(v)`` returns ``True`` for *any* non-empty string, which
      would flip unchecked options back on at the next read.
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off", ""):
            return False
        # Unknown spelling — fall through to ``default``.
    return default

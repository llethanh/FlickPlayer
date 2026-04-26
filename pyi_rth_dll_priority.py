"""PyInstaller runtime hook: force the bundle's `_internal/` to win the
Windows DLL lookup race.

Why this exists: when img_player is deployed on a workstation that
already has VFX tooling installed (Nuke, RV, DaVinci Resolve, Maya,
Houdini…), those apps tend to put their `bin/` folder on `PATH` —
which means their copy of `OpenEXR.dll`, `Imath.dll`, `IlmImf.dll` or
even `OpenImageIO.dll` can win the lookup against ours. The symptom is
the cryptic "ImportError: DLL load failed while importing OpenImageIO:
La procédure spécifiée est introuvable." (a function we expect to find
in the loaded DLL doesn't exist there because it's the wrong version).

The fix: at runtime, *before* any heavy import, prepend `_internal/` to
both `PATH` and the modern Win 3.8 DLL search path. PyInstaller already
puts our DLLs there — we just need to make sure Windows looks at our
copy first.

PyInstaller bootstrap calls this hook before `__main__.py` runs, so any
`import OpenImageIO` later in the program sees the fixed paths.
"""

import os
import sys


def _patch_dll_search() -> None:
    if sys.platform != "win32":
        return
    if not getattr(sys, "frozen", False):
        return  # not running inside a PyInstaller bundle

    bundle_dir = os.path.dirname(sys.executable)
    internal_dir = os.path.join(bundle_dir, "_internal")
    if not os.path.isdir(internal_dir):
        return

    # 1. Legacy: prepend to PATH so any code path that still resolves
    #    DLLs via the old SearchPath() finds ours first.
    os.environ["PATH"] = internal_dir + os.pathsep + os.environ.get("PATH", "")

    # 2. Modern (Python 3.8+ on Windows): register the directory with
    #    the AddDllDirectory API so LoadLibraryEx with
    #    LOAD_LIBRARY_SEARCH_USER_DIRS finds it.
    add = getattr(os, "add_dll_directory", None)
    if add is not None:
        try:
            add(internal_dir)
        except OSError:
            # Non-fatal — the PATH change above is the real safety net.
            pass


_patch_dll_search()

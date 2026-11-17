# -*- coding: utf-8 -*-
"""Frozen-aware product paths (P4 packaging).

Dev layout (run from source)::

    files/product/
        app.py  src/  ui/  models/  data/  outputs/  uploads/

PyInstaller one-folder layout (``VehicleCount`` bundle)::

    VehicleCount/            <- the portable folder users unzip
        VehicleCount         <- the exe
        _internal/           <- sys._MEIPASS: Python libs + frozen
                                src/*.py (PYZ) + datas (ui/ models/
                                data/) + bundled binaries (ffmpeg)

So in dev, PRODUCT is the parent of this file's directory; when
frozen, PRODUCT is sys._MEIPASS (PyInstaller 6 puts datas inside
_internal, not next to the exe). Outputs / uploads / param presets
are written under PRODUCT as well, so they land in _internal/ next
to the data they belong to — still inside the portable folder.
"""

import os
import sys


def is_frozen():
    """True when running inside the PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def product_dir():
    """Absolute path of the product root (dev source tree or bundle)."""
    if is_frozen():
        # PyInstaller 6 one-folder: datas land in _internal.
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_bin_dir():
    """Directory holding bundled helper binaries (ffmpeg). In the
    frozen bundle that is _internal (== product_dir()); in dev it is
    the product root, where no bundled ffmpeg exists (system ffmpeg
    is used instead)."""
    return product_dir()

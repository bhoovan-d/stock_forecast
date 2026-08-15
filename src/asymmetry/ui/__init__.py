"""Local control panel — every CLI command, driven from a browser.

The UI is a thin shell over the existing commands rather than a second implementation of
them. It binds to loopback only, and the browser never sends a command line: it sends a
command id and a field map, and the server builds ``argv`` from the registry in
:mod:`asymmetry.ui.commands`.
"""

from .server import serve

__all__ = ["serve"]

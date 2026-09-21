"""
SlidgeMAX — XMPP gateway for MAX messenger using Slidge + PyMax.
"""

from __future__ import annotations

__version__ = "0.2.0"

from .gateway import Gateway

__all__ = ["Gateway", "__version__"]

"""Authentication module for Google Maps.

Google Maps does NOT require a Google account login for basic search/place
operations. However, it DOES require valid browser cookies for session
tracking. This module manages that cookie session.
"""

from __future__ import annotations

from .session import CookieSession

__all__ = ["CookieSession"]

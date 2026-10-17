"""ASCII banner for wp-checker."""

from __future__ import annotations

__all__ = ["banner", "render_banner"]

_ART = r"""
                                 .__                   __
__  _  ________             ____ |  |__   ____   ____ |  | __ ___________ 
\ \/ \/ /\____ \   ______ _/ ___\|  |  \_/ __ \_/ ___\|  |/ // __ \_  __ \
 \     / |  |_> > /_____/ \  \___|   Y  \  ___/\  \___|    <\  ___/|  | \/
  \/\_/  |   __/           \___  >___|  /\___  >\___  >__|_ \\___  >__|   
         |__|                  \/     \/     \/     \/     \/    \/
"""

_LINKS = "[+] github.com/lalaio1/wp-checker      [+] telegram: t.me/lalaio1"

banner = _ART + "\n" + _LINKS + "\n"


def render_banner(version: str = "") -> str:
    """Return the banner text with an optional version tag."""
    title = f"wp-checker v{version}" if version else "wp-checker"
    return f"{_ART}\n      {title}\n{_LINKS}\n"

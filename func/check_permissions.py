"""Permission, role and plugin-install capability enumeration.

Once a credential pair is confirmed valid the scanner can answer three
questions that matter during a WordPress engagement:

* which **role** does the account hold (Administrator / Editor / ...)?
* which **capabilities** does it really have (probed, not guessed)?
* is the account able to **install new plugins** (``install_plugins``),
  upload plugin ZIPs (``upload_plugins``) and update the core
  (``update_core``)?

Every probe is a plain ``GET`` against a core admin page: no write action, no
plugin installation, nothing that changes the target.
"""

from __future__ import annotations

import re

__all__ = ["probe_capabilities", "infer_role", "CAPABILITY_PAGES"]

DENY_MARKERS = (
    "not allowed to access this page",
    "you do not have sufficient permissions",
    "sorry, you are not allowed",
    "need a higher level of permission",
    "insufficient permissions",
    "you need a higher level",
    "não tem permissão",
    "no tienes permisos",
)

LOGIN_FORM_MARKERS = (
    'id="loginform"',
    "user_login",
    "wp-submit",
)

# core capability -> representative admin page
CAPABILITY_PAGES: list[tuple[str, str]] = [
    ("manage_options", "/wp-admin/options-general.php"),
    ("list_users", "/wp-admin/users.php"),
    ("activate_plugins", "/wp-admin/plugins.php"),
    ("edit_posts", "/wp-admin/edit.php"),
    ("edit_pages", "/wp-admin/edit.php?post_type=page"),
    ("upload_files", "/wp-admin/upload.php"),
    ("moderate_comments", "/wp-admin/edit-comments.php"),
    ("manage_categories", "/wp-admin/edit-tags.php?taxonomy=category"),
]

INSTALL_PROBES: list[tuple[str, str, tuple[str, ...]]] = [
    ("install_plugins", "/wp-admin/plugin-install.php", ("search-plugins", "plugin-install-tab", "install-plugin")),
    ("upload_plugins", "/wp-admin/plugin-install.php?tab=upload", ("pluginzip", "upload-plugin")),
    ("update_core", "/wp-admin/update-core.php", ("update-core", "core-update", "button-primary")),
]

# admin menu entry -> implied capability (used as a fast path, no extra request)
MENU_CAPABILITIES = {
    "menu-settings": "manage_options",
    "menu-users": "list_users",
    "menu-plugins": "activate_plugins",
    "menu-appearance": "switch_themes",
    "menu-posts": "edit_posts",
    "menu-media": "upload_files",
    "menu-comments": "moderate_comments",
}

_DISPLAY_NAME_RE = re.compile(r'class="display-name"[^>]*>([^<]{1,80})<', re.IGNORECASE)
_MENU_ID_RE = re.compile(r'id="(menu-[a-z0-9_-]+)"', re.IGNORECASE)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _body(response) -> str:
    try:
        return response.text or ""
    except Exception:
        return ""


def _denied(response) -> bool:
    if response is None:
        return True
    if response.status_code >= 400:
        return True
    body = _body(response).lower()
    if any(marker in body for marker in DENY_MARKERS):
        return True
    # a redirected login form means the session died
    if "wp-login.php" in getattr(response, "url", "") and any(
        marker in body for marker in LOGIN_FORM_MARKERS
    ):
        return True
    return False


def extract_menu_ids(html: str) -> set[str]:
    if not html:
        return set()
    return {match.group(1).lower() for match in _MENU_ID_RE.finditer(html[:600_000])}


def extract_display_name(html: str) -> str:
    if not html:
        return ""
    match = _DISPLAY_NAME_RE.search(html[:400_000])
    if match:
        return match.group(1).strip()[:80]
    return ""


def infer_role(capabilities: dict[str, bool], menu_ids: set[str] | None = None) -> str:
    """Map the probed capability matrix to the closest core role."""
    if capabilities.get("manage_options") or capabilities.get("activate_plugins"):
        return "Administrator"
    if capabilities.get("edit_posts"):
        if capabilities.get("edit_pages") or capabilities.get("moderate_comments"):
            return "Editor"
        if capabilities.get("upload_files"):
            return "Author"
        return "Contributor"
    if capabilities.get("upload_files") or capabilities.get("edit_pages"):
        return "Custom role"
    if capabilities.get("moderate_comments") or capabilities.get("list_users"):
        return "Custom role"
    if menu_ids and (menu_ids - {"menu-dashboard", "menu-home"}):
        return "Custom role"
    return "Subscriber"


def _probe(http, session, url: str, markers: tuple[str, ...] = (), verbose: bool = False, log=None, headers: dict | None = None):
    response = http.get(url, session=session, headers=headers)
    if verbose and log:
        status = getattr(response, "status_code", "ERR")
        log(f"    probe {url.split('/wp-admin/')[-1]} -> {status}")
    return response


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #
def probe_capabilities(
    http,
    base_url: str,
    *,
    admin_html: str | None = None,
    session=None,
    check_capabilities: bool = True,
    check_plugin_install: bool = True,
    verbose: bool = False,
    log=None,
    extra_headers: dict | None = None,
    abort=None,
) -> dict:
    """Return a dict with role, capabilities and plugin-install capability."""
    session = session or http.session()
    result: dict = {
        "role": "Unknown",
        "admin": False,
        "capabilities": {},
        "capabilities_list": [],
        "plugin_install": "Unknown",
        "plugin_install_caps": {},
        "display_name": "",
        "probes": 0,
        "denied": [],
    }

    if admin_html is None:
        response = _probe(http, session, f"{base_url}/wp-admin/", verbose=verbose, log=log, headers=extra_headers)
        result["probes"] += 1
        if _denied(response):
            result["role"] = "Unknown (admin access denied)"
            return result
        admin_html = _body(response)

    result["display_name"] = extract_display_name(admin_html)
    menu_ids = extract_menu_ids(admin_html)
    capabilities: dict[str, bool] = {}

    if check_capabilities:
        for menu_id, capability in MENU_CAPABILITIES.items():
            if menu_id in menu_ids:
                capabilities[capability] = True

        for capability, path in CAPABILITY_PAGES:
            if capabilities.get(capability):
                continue
            if abort is not None and abort.is_set():
                break
            response = _probe(http, session, f"{base_url}{path}", verbose=verbose, log=log, headers=extra_headers)
            result["probes"] += 1
            ok = not _denied(response)
            capabilities[capability] = ok
            if not ok:
                result["denied"].append(capability)

        result["role"] = infer_role(capabilities, menu_ids)
        result["admin"] = bool(capabilities.get("manage_options") or capabilities.get("activate_plugins"))

    if check_plugin_install:
        install_caps: dict[str, bool] = {}
        for capability, path, markers in INSTALL_PROBES:
            if abort is not None and abort.is_set():
                break
            response = _probe(http, session, f"{base_url}{path}", verbose=verbose, log=log, headers=extra_headers)
            result["probes"] += 1
            body = _body(response)
            lowered = body.lower()
            denied = _denied(response)
            if denied:
                install_caps[capability] = False
                continue
            if any(marker in lowered for marker in markers):
                install_caps[capability] = True
            elif "disallow_file_mods" in lowered or "file modifications are not allowed" in lowered:
                install_caps[capability] = False
            else:
                # page loaded without the expected UI: treat as unavailable
                install_caps[capability] = False

        result["plugin_install_caps"] = install_caps
        enabled = [name.replace("_plugins", "").replace("update_core", "update-core") for name, ok in install_caps.items() if ok]
        if install_caps.get("install_plugins"):
            pretty = ", ".join(
                {
                    "install": "install",
                    "upload": "upload",
                    "update-core": "update-core",
                }.get(item, item)
                for item in enabled
            )
            result["plugin_install"] = f"Yes ({pretty})" if pretty else "Yes"
        else:
            result["plugin_install"] = "No"

    result["capabilities"] = capabilities
    result["capabilities_list"] = sorted(name for name, ok in capabilities.items() if ok)
    return result

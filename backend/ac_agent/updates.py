"""Update notice: asks GitHub (only if a repository is configured and the user
did not switch it off) whether a newer StintPulse release exists. Sends no
data except the HTTP request itself; never downloads or installs anything."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

from . import APP_NAME, UPDATE_REPO, __version__

CACHE_S = 6 * 3600
_cache: dict = {}
_lock = threading.Lock()


def parse_version(text):
    numbers = re.findall(r"\d+", text or "")[:3]
    return tuple(int(n) for n in numbers) + (0,) * (3 - len(numbers)) if numbers else None


def newer(candidate, current=__version__):
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


def _latest(repo):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{__version__}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        data = json.load(response)
    return {
        "version": str(data.get("tag_name", "")).lstrip("vV"),
        "url": data.get("html_url") or f"https://github.com/{repo}/releases/latest",
        "published_at": data.get("published_at"),
    }


def check(enabled, repo=None, fetch=None, now=None):
    repo = UPDATE_REPO if repo is None else repo
    fetch = fetch or _latest
    info = {
        "name": APP_NAME,
        "version": __version__,
        "update_check": bool(enabled and repo),
        "download_url": f"https://github.com/{repo}/releases/latest" if repo else None,
        "update": None,
        "error": None,
    }
    if not enabled or not repo:
        return info
    now = time.time() if now is None else now
    with _lock:
        cached = _cache.get(repo)
        if not cached or now - cached[0] > CACHE_S:
            try:
                cached = (now, fetch(repo), None)
            except Exception as exc:  # offline, rate limit, no release yet
                cached = (now - CACHE_S + 600, None, type(exc).__name__)  # retry in 10 min
            _cache[repo] = cached
    _, latest, error = cached
    info["error"] = error
    if latest and newer(latest["version"]):
        info["update"] = latest
    return info

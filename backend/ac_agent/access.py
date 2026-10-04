"""LAN access code check with brute-force protection.

An 8-digit code has 10^8 combinations; without a limit it could be guessed in
the LAN. After MAX_FAILURES wrong attempts a client IP is locked for LOCK_S.
The PC itself (loopback) never needs the code.
"""

import ipaddress
import secrets
import threading
import time

MAX_FAILURES = 10
LOCK_S = 60.0


def is_loopback(conn):
    host = conn.client.host if conn.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def code_matches(provided, code):
    if not code or not provided:
        return False
    return secrets.compare_digest(provided.encode("utf-8"), code.encode("utf-8"))


class AccessGuard:
    """Counts only DISTINCT wrong codes per client.

    A dashboard polls several endpoints per second. Requests without a code
    (before the user typed it) and repetitions of the same stale/mistyped code
    are not new guesses and must not lock the device. Guessing still requires
    MAX_FAILURES different codes per lock period.
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.failures = {}  # ip -> {"codes": set of wrong codes, "until": t}

    def locked_for(self, ip):
        with self.lock:
            entry = self.failures.get(ip)
            if not entry:
                return 0.0
            return max(0.0, entry["until"] - self.clock())

    def check(self, ip, provided, code):
        """Returns 'ok', 'locked' or 'denied'."""
        if self.locked_for(ip) > 0:
            return "locked"
        if code_matches(provided, code):
            with self.lock:
                self.failures.pop(ip, None)
            return "ok"
        if not provided:
            return "denied"  # no attempt at all: never counts
        with self.lock:
            entry = self.failures.setdefault(ip, {"codes": set(), "until": 0.0})
            if provided not in entry["codes"]:
                entry["codes"].add(provided)
                if len(entry["codes"]) % MAX_FAILURES == 0:
                    entry["until"] = self.clock() + LOCK_S
        return "denied"

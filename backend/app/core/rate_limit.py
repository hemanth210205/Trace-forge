"""In-process sliding-window limiter for failed login attempts, with atomic attempt reservation.

A login first *reserves* an attempt in both budgets (per client IP + email, and per client IP) in one
locked step, before any password hashing. In-flight reservations count against the budget exactly like
recorded failures, so concurrent requests cannot all pass the check before any failure is recorded. The
reservation is then finalized: a failed login converts it into a recorded failure; a successful login
releases it (and clears that IP + email's failures, but never the IP-wide failures). Leaving the
``with`` block without finalizing, e.g. on an unexpected exception, releases it. As a backstop, a
reservation that is never finalized expires after ``reservation_ttl_seconds``.

The TTL is not an upper bound on how long a login can run: the database connection wait (pool timeout
30s, connect timeout 10s) is bounded, but query time is not (no statement timeout is configured). If a
login outlives the TTL, its slot is freed early and one more attempt can be reserved for that key, a
temporary budget overshoot of one attempt per such request. Its late result is still applied safely
(a failure is still recorded; nothing belonging to another request is touched).

Deployment limits: state lives in one Python process. It is not shared between uvicorn workers,
processes or hosts, and it resets on restart, so with N workers an attacker gets up to N times the
configured budget. It is suitable for the current single-process local deployment only; a multi-worker
or distributed deployment needs a shared store (for example Redis or PostgreSQL) behind the same
interface.

Memory is bounded: each budget tracks at most ``max_keys`` keys, and each key holds at most
``max_failures`` entries (recorded failures plus reservations). To make room for a new key at the cap,
the least recently used key *without live reservations* is evicted (after dropping expired
reservations); its failure history is lost. A key holding live reservations is never evicted. If every
key holds live reservations, the new reservation is refused with ``RateLimited(1)``.
"""

import itertools
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType

# Exceeds a normal login (connection wait of at most 30s pool + 10s connect, a short query, one argon2
# verification), but query time itself is unbounded, so a stalled login can outlive it (see module doc).
DEFAULT_RESERVATION_TTL_SECONDS = 60.0


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class CapacityExhausted(RateLimited):
    """Every tracked key holds live reservations, so no key can be evicted to make room."""

    def __init__(self) -> None:
        super().__init__(1)


@dataclass
class _Entry:
    failures: deque[float]
    pending: dict[int, float] = field(default_factory=dict)  # reservation id -> reserved at


class FailureLimiter:
    """One sliding-window failure budget (for example per client IP).

    ``lock`` is re-entrant and may be shared between budgets so that a caller can update several of
    them atomically under a single lock, which also rules out lock-ordering deadlocks.
    """

    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        max_keys: int,
        clock: Callable[[], float] = time.monotonic,
        *,
        reservation_ttl_seconds: float = DEFAULT_RESERVATION_TTL_SECONDS,
        lock: "threading.RLock | None" = None,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.clock = clock
        self.reservation_ttl_seconds = reservation_ttl_seconds
        self.lock = lock or threading.RLock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    def _live(self, key: str, now: float) -> _Entry | None:
        """The key's entry with expired failures and stale reservations dropped; None if nothing is left."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        while entry.failures and entry.failures[0] <= now - self.window_seconds:
            entry.failures.popleft()
        for reservation_id, reserved_at in list(entry.pending.items()):
            if reserved_at <= now - self.reservation_ttl_seconds:
                del entry.pending[reservation_id]
        if not entry.failures and not entry.pending:
            del self._entries[key]
            return None
        return entry

    def _victim(self, key: str, now: float) -> str | None:
        """What must be evicted so ``key`` can have an entry: None if nothing (the key is live or there is
        room), else the least recently used key without live reservations. Raises CapacityExhausted if every
        key holds live reservations. Only drops expired data; never evicts."""
        if self._live(key, now) is not None or len(self._entries) < self.max_keys:
            return None
        # LRU first. The scan passes over keys with live reservations only, which are bounded by the
        # in-flight logins. It stops at the first dict mutation (``_live`` deleting an empty entry).
        for candidate in self._entries:
            entry = self._live(candidate, now)
            if entry is None:
                return None  # TTL/window cleanup emptied this entry and freed a slot
            if not entry.pending:
                return candidate
        raise CapacityExhausted

    def _entry(self, key: str, now: float) -> _Entry:
        victim = self._victim(key, now)
        if victim is not None:
            del self._entries[victim]
        entry = self._entries.get(key)
        if entry is None:
            entry = _Entry(failures=deque(maxlen=self.max_failures))
            self._entries[key] = entry
        self._entries.move_to_end(key)
        return entry

    def ensure_capacity(self, key: str, now: float | None = None) -> None:
        """Raises CapacityExhausted if ``key`` could not get an entry right now. Evicts nothing."""
        with self.lock:
            self._victim(key, self.clock() if now is None else now)

    def retry_after(self, key: str, now: float | None = None) -> int | None:
        """Seconds until another attempt is allowed, or None if the key is under its limit."""
        with self.lock:
            now = self.clock() if now is None else now
            entry = self._live(key, now)
            if entry is None or len(entry.failures) + len(entry.pending) < self.max_failures:
                return None
            if len(entry.failures) >= self.max_failures:
                return max(1, math.ceil(entry.failures[0] + self.window_seconds - now))
            # Part of the budget is held by in-flight attempts, which resolve within one password check.
            return 1

    def reserve(self, key: str, reservation_id: int, now: float | None = None) -> None:
        """Holds one slot. Callers check ``retry_after`` and ``ensure_capacity`` under the same lock and
        with the same ``now`` first (see LoginRateLimiter.reserve). Raises CapacityExhausted otherwise."""
        with self.lock:
            now = self.clock() if now is None else now
            self._entry(key, now).pending[reservation_id] = now

    def commit_failure(self, key: str, reservation_id: int) -> None:
        """Turns this request's reservation into a recorded failure. Still records the failure if the
        reservation already expired. Only ever removes its own reservation id (ids are never reused)."""
        with self.lock:
            now = self.clock()
            entry = self._live(key, now)
            if entry is None:
                # Late finalization after the entry was cleaned up: record into a fresh entry if one can be
                # allocated without evicting anyone's live reservation; otherwise drop this late record.
                try:
                    entry = self._entry(key, now)
                except CapacityExhausted:
                    return
            else:
                self._entries.move_to_end(key)
            entry.pending.pop(reservation_id, None)
            entry.failures.append(now)

    def release(self, key: str, reservation_id: int, *, clear_failures: bool = False) -> None:
        with self.lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.pending.pop(reservation_id, None)
            if clear_failures:
                entry.failures.clear()
            self._live(key, self.clock())

    def record_failure(self, key: str) -> None:
        with self.lock:
            now = self.clock()
            self._entry(key, now).failures.append(now)

    def pending_count(self, key: str) -> int:
        with self.lock:
            entry = self._live(key, self.clock())
            return len(entry.pending) if entry else 0

    def failure_count(self, key: str) -> int:
        with self.lock:
            entry = self._live(key, self.clock())
            return len(entry.failures) if entry else 0

    def reset(self, key: str | None = None) -> None:
        with self.lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)

    def __len__(self) -> int:
        with self.lock:
            return len(self._entries)


class LoginAttempt:
    """A reserved login attempt. Finalize with ``fail()`` or ``succeed()``; otherwise leaving the
    ``with`` block releases it. Finalization is idempotent: only the first call has an effect."""

    def __init__(self, limiter: "LoginRateLimiter", ip: str, account_key: str, reservation_id: int) -> None:
        self._limiter = limiter
        self.ip = ip
        self.account_key = account_key
        self.reservation_id = reservation_id
        self._finalized = False

    def _finalize(self, action: Callable[["LoginAttempt"], None]) -> None:
        if self._finalized:
            return
        self._finalized = True  # set first: a failure inside ``action`` must not cause a second finalization
        action(self)

    def fail(self) -> None:
        self._finalize(self._limiter._commit_failure)

    def succeed(self) -> None:
        self._finalize(self._limiter._release_success)

    def release(self) -> None:
        self._finalize(self._limiter._release)

    def __enter__(self) -> "LoginAttempt":
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


class LoginRateLimiter:
    """Two budgets, updated together under one shared lock: failures per (client IP, email) and failures
    per client IP across all emails."""

    def __init__(self, *, per_account: FailureLimiter, per_ip: FailureLimiter) -> None:
        if per_account.lock is not per_ip.lock:
            raise ValueError("per_account and per_ip must share one lock so reservations are atomic")
        self.per_account = per_account
        self.per_ip = per_ip
        self._lock = per_account.lock
        self._ids = itertools.count()

    @classmethod
    def build(
        cls,
        *,
        max_failures: int,
        max_failures_per_ip: int,
        window_seconds: float,
        max_keys: int,
        reservation_ttl_seconds: float = DEFAULT_RESERVATION_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> "LoginRateLimiter":
        lock = threading.RLock()
        options = {"reservation_ttl_seconds": reservation_ttl_seconds, "lock": lock}
        return cls(
            per_account=FailureLimiter(max_failures, window_seconds, max_keys, clock, **options),
            per_ip=FailureLimiter(max_failures_per_ip, window_seconds, max_keys, clock, **options),
        )

    @staticmethod
    def account_key(ip: str, email: str) -> str:
        return f"{ip}\x00{email}"

    def reserve(self, ip: str, email: str) -> LoginAttempt:
        """Atomically checks both budgets and reserves one attempt in each. Raises RateLimited if either
        budget is exhausted, or CapacityExhausted (a RateLimited with retry_after=1) if either budget cannot
        allocate an entry; in both cases nothing is reserved in either budget."""
        account_key = self.account_key(ip, email)
        with self._lock:
            # One timestamp for every check and write, so a check cannot pass and its write then fail.
            now = self.per_account.clock()
            waits = [
                wait
                for wait in (self.per_account.retry_after(account_key, now), self.per_ip.retry_after(ip, now))
                if wait is not None
            ]
            if waits:
                raise RateLimited(max(waits))
            # Both budgets must be able to hold their entry before either is written: no partial reservation.
            self.per_account.ensure_capacity(account_key, now)
            self.per_ip.ensure_capacity(ip, now)
            reservation_id = next(self._ids)
            self.per_account.reserve(account_key, reservation_id, now)
            self.per_ip.reserve(ip, reservation_id, now)
        return LoginAttempt(self, ip, account_key, reservation_id)

    def retry_after(self, ip: str, email: str) -> int | None:
        """Read-only view of the current wait, counting in-flight reservations."""
        with self._lock:
            waits = [
                wait
                for wait in (self.per_account.retry_after(self.account_key(ip, email)), self.per_ip.retry_after(ip))
                if wait is not None
            ]
            return max(waits) if waits else None

    def _commit_failure(self, attempt: LoginAttempt) -> None:
        with self._lock:
            self.per_account.commit_failure(attempt.account_key, attempt.reservation_id)
            self.per_ip.commit_failure(attempt.ip, attempt.reservation_id)

    def _release_success(self, attempt: LoginAttempt) -> None:
        # The IP + email failures are cleared; IP-wide failures are kept, so one valid login cannot reset
        # an IP's budget for spraying other emails.
        with self._lock:
            self.per_account.release(attempt.account_key, attempt.reservation_id, clear_failures=True)
            self.per_ip.release(attempt.ip, attempt.reservation_id)

    def _release(self, attempt: LoginAttempt) -> None:
        with self._lock:
            self.per_account.release(attempt.account_key, attempt.reservation_id)
            self.per_ip.release(attempt.ip, attempt.reservation_id)

    def set_clock(self, clock: Callable[[], float]) -> None:
        with self._lock:
            self.per_account.clock = clock
            self.per_ip.clock = clock

    def reset(self) -> None:
        with self._lock:
            self.per_account.reset()
            self.per_ip.reset()

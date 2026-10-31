"""Unit tests for the in-process failure limiter and atomic reservations (no HTTP)."""

import random
import threading

import pytest

from app.core.rate_limit import CapacityExhausted, FailureLimiter, LoginAttempt, LoginRateLimiter, RateLimited

IP, EMAIL = "1.2.3.4", "a@example.com"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(clock: FakeClock, per_account: int = 3, per_ip: int = 5, ttl: float = 60) -> LoginRateLimiter:
    return LoginRateLimiter.build(
        max_failures=per_account,
        max_failures_per_ip=per_ip,
        window_seconds=60,
        max_keys=100,
        reservation_ttl_seconds=ttl,
        clock=clock,
    )


# --- FailureLimiter: sliding window and memory bounds ---------------------------------------------------


def test_limits_after_max_failures_and_reports_retry_after() -> None:
    clock = FakeClock()
    limiter = FailureLimiter(max_failures=3, window_seconds=60, max_keys=100, clock=clock)
    for _ in range(2):
        limiter.record_failure("k")
        assert limiter.retry_after("k") is None
    clock.now += 10
    limiter.record_failure("k")
    assert limiter.retry_after("k") == 50  # the first failure leaves the window 60s after it happened


def test_sliding_window_releases_old_failures() -> None:
    clock = FakeClock()
    limiter = FailureLimiter(max_failures=2, window_seconds=60, max_keys=100, clock=clock)
    limiter.record_failure("k")
    clock.now += 30
    limiter.record_failure("k")
    assert limiter.retry_after("k") == 30
    clock.now += 30
    assert limiter.retry_after("k") is None
    clock.now += 30
    assert limiter.retry_after("k") is None
    assert len(limiter) == 0


def test_key_count_is_bounded_with_lru_eviction() -> None:
    limiter = FailureLimiter(max_failures=1, window_seconds=60, max_keys=100, clock=FakeClock())
    for i in range(250):
        limiter.record_failure(f"k{i}")
    assert len(limiter) == 100
    assert limiter.retry_after("k0") is None  # evicted
    assert limiter.retry_after("k249") is not None


def test_per_key_failures_are_bounded() -> None:
    limiter = FailureLimiter(max_failures=3, window_seconds=60, max_keys=100, clock=FakeClock())
    for _ in range(1000):
        limiter.record_failure("k")
    assert limiter.failure_count("k") == 3


def test_budgets_must_share_one_lock() -> None:
    with pytest.raises(ValueError, match="share one lock"):
        LoginRateLimiter(per_account=FailureLimiter(1, 60, 100), per_ip=FailureLimiter(1, 60, 100))


# --- Reservations ---------------------------------------------------------------------------------------


def test_reservations_count_against_the_budget_until_finalized() -> None:
    limiter = build(FakeClock(), per_account=3)
    attempts = [limiter.reserve(IP, EMAIL) for _ in range(3)]
    with pytest.raises(RateLimited) as exc:
        limiter.reserve(IP, EMAIL)
    assert exc.value.retry_after == 1  # held by in-flight attempts, not by recorded failures
    attempts[0].release()
    limiter.reserve(IP, EMAIL)  # a released slot is reusable


def test_failed_attempts_become_failures_with_window_retry_after() -> None:
    clock = FakeClock()
    limiter = build(clock, per_account=3)
    for _ in range(3):
        limiter.reserve(IP, EMAIL).fail()
    assert limiter.per_account.failure_count(limiter.account_key(IP, EMAIL)) == 3
    assert limiter.per_account.pending_count(limiter.account_key(IP, EMAIL)) == 0
    clock.now += 15
    with pytest.raises(RateLimited) as exc:
        limiter.reserve(IP, EMAIL)
    assert exc.value.retry_after == 45
    clock.now += 45
    limiter.reserve(IP, EMAIL)  # window reset


def test_rejected_reservation_reserves_nothing_in_either_budget() -> None:
    limiter = build(FakeClock(), per_account=3, per_ip=2)
    limiter.reserve(IP, "x@example.com").fail()
    limiter.reserve(IP, "y@example.com").fail()
    with pytest.raises(RateLimited):
        limiter.reserve(IP, EMAIL)  # per-IP budget exhausted
    assert limiter.per_account.pending_count(limiter.account_key(IP, EMAIL)) == 0
    assert limiter.per_ip.pending_count(IP) == 0


def test_success_releases_and_clears_account_failures_but_keeps_ip_failures() -> None:
    limiter = build(FakeClock(), per_account=3, per_ip=5)
    for _ in range(2):
        limiter.reserve(IP, EMAIL).fail()
    limiter.reserve(IP, EMAIL).succeed()
    key = limiter.account_key(IP, EMAIL)
    assert limiter.per_account.failure_count(key) == 0
    assert limiter.per_account.pending_count(key) == 0
    assert limiter.per_ip.failure_count(IP) == 2
    assert limiter.per_ip.pending_count(IP) == 0


def test_success_does_not_drop_other_in_flight_reservations() -> None:
    limiter = build(FakeClock(), per_account=3)
    other = limiter.reserve(IP, EMAIL)
    limiter.reserve(IP, EMAIL).succeed()
    assert limiter.per_account.pending_count(limiter.account_key(IP, EMAIL)) == 1
    other.fail()
    assert limiter.per_account.failure_count(limiter.account_key(IP, EMAIL)) == 1


def test_exception_inside_with_block_releases_the_reservation() -> None:
    limiter = build(FakeClock(), per_account=1)
    with pytest.raises(RuntimeError):
        with limiter.reserve(IP, EMAIL):
            raise RuntimeError("database went away")
    assert limiter.per_ip.pending_count(IP) == 0
    assert limiter.per_ip.failure_count(IP) == 0
    limiter.reserve(IP, EMAIL)  # budget fully available again


def test_finalization_is_idempotent() -> None:
    limiter = build(FakeClock(), per_account=3)
    with limiter.reserve(IP, EMAIL) as attempt:
        attempt.fail()
        attempt.fail()
        attempt.succeed()
    assert limiter.per_account.failure_count(limiter.account_key(IP, EMAIL)) == 1


def test_exception_during_finalization_does_not_wedge_the_lock_and_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    limiter = build(clock, per_account=1, ttl=30)

    def broken(*_args: object) -> None:
        raise RuntimeError("bug in finalization")

    attempt = limiter.reserve(IP, EMAIL)
    monkeypatch.setattr(limiter.per_ip, "commit_failure", broken)
    with pytest.raises(RuntimeError):
        attempt.fail()
    monkeypatch.undo()

    # The lock was released (another thread can use the limiter) ...
    done = threading.Event()
    threading.Thread(target=lambda: (limiter.retry_after(IP, EMAIL), done.set())).start()
    assert done.wait(timeout=2)
    # ... the half-finalized attempt is not finalized twice by the with-block exit ...
    attempt.release()
    assert limiter.per_ip.pending_count(IP) == 1
    # ... and the stuck per-IP reservation expires after the TTL instead of holding the budget forever.
    clock.now += 31
    assert limiter.per_ip.pending_count(IP) == 0


def test_abandoned_reservation_expires_after_ttl() -> None:
    clock = FakeClock()
    limiter = build(clock, per_account=1, ttl=30)
    limiter.reserve(IP, EMAIL)  # never finalized, never released
    with pytest.raises(RateLimited):
        limiter.reserve(IP, EMAIL)
    clock.now += 30
    limiter.reserve(IP, EMAIL)


def test_late_failure_after_reservation_expiry_is_still_recorded() -> None:
    clock = FakeClock()
    limiter = build(clock, per_account=3, ttl=30)
    attempt = limiter.reserve(IP, EMAIL)
    clock.now += 31
    attempt.fail()
    assert limiter.per_account.failure_count(limiter.account_key(IP, EMAIL)) == 1


def test_pending_reservations_per_key_are_bounded() -> None:
    limiter = build(FakeClock(), per_account=3, per_ip=1000)
    for _ in range(3):
        limiter.reserve(IP, EMAIL)
    for _ in range(100):
        with pytest.raises(RateLimited):
            limiter.reserve(IP, EMAIL)
    assert limiter.per_account.pending_count(limiter.account_key(IP, EMAIL)) == 3


# --- Memory-cap eviction never discards live reservations (R1) -----------------------------------------


def split_limiter(clock: FakeClock, *, account_keys: int, ip_keys: int, ttl: float = 60) -> LoginRateLimiter:
    """Budgets with different key caps (same shared lock), to exhaust one budget's capacity at a time."""
    lock = threading.RLock()
    return LoginRateLimiter(
        per_account=FailureLimiter(5, 60, account_keys, clock, reservation_ttl_seconds=ttl, lock=lock),
        per_ip=FailureLimiter(50, 60, ip_keys, clock, reservation_ttl_seconds=ttl, lock=lock),
    )


def keys(limiter: FailureLimiter) -> list[str]:
    return list(limiter._entries)


def test_entry_with_live_reservation_is_never_evicted() -> None:
    limiter = FailureLimiter(5, 60, max_keys=3, clock=FakeClock())
    limiter.reserve("held", 1)  # least recently used, but holds a live reservation
    limiter.record_failure("idle-1")
    limiter.record_failure("idle-2")
    limiter.reserve("new", 2)
    assert keys(limiter) == ["held", "idle-2", "new"]
    assert limiter.pending_count("held") == 1


def test_least_recently_used_inactive_entry_is_selected() -> None:
    limiter = FailureLimiter(5, 60, max_keys=4, clock=FakeClock())
    limiter.reserve("held-a", 1)
    limiter.record_failure("idle-old")
    limiter.reserve("held-b", 2)
    limiter.record_failure("idle-new")
    limiter.record_failure("new")
    assert keys(limiter) == ["held-a", "held-b", "idle-new", "new"]  # only idle-old went


def test_all_entries_with_live_reservations_refuses_new_key() -> None:
    limiter = FailureLimiter(5, 60, max_keys=3, clock=FakeClock())
    for i in range(3):
        limiter.reserve(f"held-{i}", i)
    with pytest.raises(CapacityExhausted) as exc:
        limiter.reserve("new", 99)
    assert exc.value.retry_after == 1 and isinstance(exc.value, RateLimited)
    assert keys(limiter) == ["held-0", "held-1", "held-2"]
    assert all(limiter.pending_count(k) == 1 for k in keys(limiter))
    limiter.reserve("held-1", 100)  # an existing key still gets a slot: it needs no new entry
    assert limiter.pending_count("held-1") == 2


def test_rejection_does_not_partially_reserve_account_budget_when_ip_budget_is_full() -> None:
    limiter = split_limiter(FakeClock(), account_keys=10, ip_keys=2)
    held = [limiter.reserve("10.0.0.1", EMAIL), limiter.reserve("10.0.0.2", EMAIL)]
    account_before = keys(limiter.per_account)
    with pytest.raises(CapacityExhausted):
        limiter.reserve("10.0.0.3", EMAIL)
    assert keys(limiter.per_account) == account_before  # nothing written to the budget that had room
    assert limiter.per_account.pending_count(limiter.account_key("10.0.0.3", EMAIL)) == 0
    assert keys(limiter.per_ip) == ["10.0.0.1", "10.0.0.2"]
    assert [limiter.per_ip.pending_count(ip) for ip in ("10.0.0.1", "10.0.0.2")] == [1, 1]
    assert held  # still live


def test_rejection_does_not_partially_reserve_ip_budget_when_account_budget_is_full() -> None:
    limiter = split_limiter(FakeClock(), account_keys=2, ip_keys=10)
    limiter.reserve(IP, "a@example.com")
    limiter.reserve(IP, "b@example.com")
    with pytest.raises(CapacityExhausted):
        limiter.reserve(IP, "c@example.com")
    assert limiter.per_ip.pending_count(IP) == 2  # not 3
    assert len(limiter.per_account) == 2


def test_existing_reservations_finalize_correctly_after_a_rejected_allocation() -> None:
    clock = FakeClock()
    limiter = split_limiter(clock, account_keys=2, ip_keys=10)
    a = limiter.reserve(IP, "a@example.com")
    b = limiter.reserve(IP, "b@example.com")
    with pytest.raises(CapacityExhausted):
        limiter.reserve(IP, "c@example.com")

    a.fail()
    b.succeed()
    key_a, key_b = limiter.account_key(IP, "a@example.com"), limiter.account_key(IP, "b@example.com")
    assert (limiter.per_account.failure_count(key_a), limiter.per_account.pending_count(key_a)) == (1, 0)
    assert len(limiter.per_account) == 1  # b's entry emptied and removed on success
    assert limiter.per_ip.failure_count(IP) == 1 and limiter.per_ip.pending_count(IP) == 0

    c = limiter.reserve(IP, "c@example.com")  # room again
    d = limiter.reserve(IP, "d@example.com")  # evicts a (now idle), the LRU inactive entry
    assert keys(limiter.per_account) == [limiter.account_key(IP, "c@example.com"), limiter.account_key(IP, "d@example.com")]
    c.release()
    d.release()
    assert key_b not in keys(limiter.per_account)


def test_expired_reservations_make_their_entry_evictable() -> None:
    clock = FakeClock()
    limiter = FailureLimiter(5, 600, max_keys=2, clock=clock, reservation_ttl_seconds=30)
    limiter.record_failure("old")  # keeps the entry alive after its reservation expires
    limiter.reserve("old", 1)
    limiter.reserve("young", 2)
    with pytest.raises(CapacityExhausted):
        limiter.reserve("new", 3)
    clock.now += 20
    limiter.reserve("young", 4)  # young refreshed: its newest reservation is live for longer
    clock.now += 10  # old's reservation (and young's first one) are now expired
    limiter.reserve("new", 5)
    assert keys(limiter) == ["young", "new"]
    assert limiter.pending_count("young") == 1


def test_late_failure_after_expiry_does_not_touch_another_requests_reservation() -> None:
    clock = FakeClock()
    limiter = build(clock, per_account=3, ttl=30)
    late = limiter.reserve(IP, EMAIL)
    clock.now += 31  # late's reservation expires and its (otherwise empty) entry is cleaned up
    current = limiter.reserve(IP, EMAIL)
    late.fail()
    key = limiter.account_key(IP, EMAIL)
    assert limiter.per_account.pending_count(key) == 1  # current's reservation intact
    assert limiter.per_account.failure_count(key) == 1  # late failure still recorded
    late.release()  # no-op after finalization
    current.release()
    assert limiter.per_account.pending_count(key) == 0 and limiter.per_account.failure_count(key) == 1


def test_late_failure_when_capacity_is_full_of_live_reservations_is_dropped_safely() -> None:
    clock = FakeClock()
    limiter = build(clock, per_account=3, per_ip=50, ttl=30)
    limiter.per_account.max_keys = limiter.per_ip.max_keys = 2
    late = limiter.reserve("10.0.0.9", EMAIL)
    clock.now += 31
    held = [limiter.reserve("10.0.0.1", EMAIL), limiter.reserve("10.0.0.2", EMAIL)]  # fill both caps
    late.fail()  # no room for its entry and nobody evictable: dropped, no exception
    assert len(limiter.per_account) == 2 and len(limiter.per_ip) == 2
    for attempt in held:
        assert limiter.per_ip.pending_count(attempt.ip) == 1


def test_memory_cap_is_never_exceeded_and_live_reservations_are_never_lost() -> None:
    clock = FakeClock()
    rng = random.Random(1234)
    limiter = LoginRateLimiter.build(
        max_failures=3, max_failures_per_ip=6, window_seconds=40, max_keys=5, reservation_ttl_seconds=15, clock=clock
    )
    live: list[tuple[LoginAttempt, float]] = []  # unfinalized attempts and when they were reserved
    ips = [f"10.0.0.{i}" for i in range(8)]
    emails = [f"u{i}@example.com" for i in range(6)]
    rejected_for_capacity = 0
    for _ in range(5000):
        op = rng.random()
        if op < 0.5:
            try:
                live.append((limiter.reserve(rng.choice(ips), rng.choice(emails)), clock.now))
            except CapacityExhausted:
                rejected_for_capacity += 1
            except RateLimited:
                pass
        elif op < 0.85 and live:
            attempt, _ = live.pop(rng.randrange(len(live)))
            rng.choice([attempt.fail, attempt.succeed, attempt.release])()
        else:
            clock.now += rng.uniform(0, 5)
        assert len(limiter.per_account) <= 5 and len(limiter.per_ip) <= 5
        for attempt, reserved_at in live:
            if clock.now - reserved_at < 15:  # unexpired: must still be held in both budgets
                assert attempt.reservation_id in limiter.per_ip._entries[attempt.ip].pending
                assert attempt.reservation_id in limiter.per_account._entries[attempt.account_key].pending
    assert rejected_for_capacity > 0  # the cap was actually reached and enforced by refusal


def test_threads_cannot_overshoot_either_budget() -> None:
    limiter = build(FakeClock(), per_account=4, per_ip=6)
    barrier = threading.Barrier(40)
    granted: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        email = EMAIL if i % 2 == 0 else f"other{i}@example.com"
        barrier.wait()
        try:
            limiter.reserve(IP, email)
        except RateLimited:
            return
        with lock:
            granted.append(email)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(granted) == 6  # per-IP budget
    assert granted.count(EMAIL) <= 4  # per-account budget

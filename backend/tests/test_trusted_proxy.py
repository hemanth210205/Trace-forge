"""Forwarded headers are believed only from configured trusted proxies (TRACEFORGE_TRUSTED_PROXIES)."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.requests import Request

from app.core.config import Settings
from app.core.proxy import client_ip, parse_networks, request_scheme
from tests.conftest import LOGIN_MAX_FAILURES, TEST_API_KEY_PEPPER, TEST_SECRET_KEY

LOGIN = "/api/v1/auth/login"
PROXY = "10.0.0.5"


def request(peer: str, *headers: tuple[str, str], scheme: str = "http") -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/", "scheme": scheme, "server": ("testserver", 80),
        "client": (peer, 50000), "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    })


NETS = parse_networks(f"{PROXY}, 192.168.0.0/16")


# --- resolver --------------------------------------------------------------------------------------------------


def test_without_trusted_proxies_the_peer_is_always_the_client() -> None:
    assert client_ip(request("203.0.113.9", ("X-Forwarded-For", "1.2.3.4")), []) == "203.0.113.9"


def test_forwarded_header_from_an_untrusted_peer_is_ignored() -> None:
    assert client_ip(request("203.0.113.9", ("X-Forwarded-For", "1.2.3.4")), NETS) == "203.0.113.9"


def test_trusted_proxy_forwards_the_client_address() -> None:
    assert client_ip(request(PROXY, ("X-Forwarded-For", "198.51.100.7")), NETS) == "198.51.100.7"


def test_client_supplied_entries_left_of_the_real_client_are_ignored() -> None:
    # The client sent "X-Forwarded-For: 1.2.3.4"; the proxy appended the address it really saw.
    assert client_ip(request(PROXY, ("X-Forwarded-For", "1.2.3.4, 198.51.100.7")), NETS) == "198.51.100.7"


def test_chained_trusted_proxies_are_skipped() -> None:
    header = ("X-Forwarded-For", "1.2.3.4, 198.51.100.7, 192.168.1.20")
    assert client_ip(request(PROXY, header), NETS) == "198.51.100.7"


def test_multiple_header_lines_are_read_in_order() -> None:
    lines = (("X-Forwarded-For", "1.2.3.4"), ("X-Forwarded-For", "198.51.100.7"))
    assert client_ip(request(PROXY, *lines), NETS) == "198.51.100.7"


@pytest.mark.parametrize("value", ["not-an-ip", "198.51.100.7, garbage", "", "   "])
def test_malformed_or_empty_entries_never_beat_the_last_trusted_hop(value: str) -> None:
    resolved = client_ip(request(PROXY, ("X-Forwarded-For", value)), NETS)
    assert resolved == PROXY


def test_ipv6_and_ipv4_mapped_addresses() -> None:
    nets = parse_networks("::1")
    assert client_ip(request("::1", ("X-Forwarded-For", "[2001:db8::7]:4711")), nets) == "2001:db8::7"
    assert client_ip(request("::1", ("X-Forwarded-For", "::ffff:198.51.100.7")), nets) == "198.51.100.7"


def test_scheme_is_taken_only_from_a_trusted_proxy() -> None:
    assert request_scheme(request(PROXY, ("X-Forwarded-Proto", "https")), NETS) == "https"
    assert request_scheme(request("203.0.113.9", ("X-Forwarded-Proto", "https")), NETS) == "http"
    assert request_scheme(request(PROXY, ("X-Forwarded-Proto", "javascript")), NETS) == "http"
    assert request_scheme(request(PROXY, ("X-Forwarded-Proto", "http, https")), NETS) == "https"


@pytest.mark.parametrize("value", ["10.0.0.5, nonsense", "10.0.0.0/99", "*"])
def test_invalid_trusted_proxy_setting_fails_at_startup(value: str) -> None:
    with pytest.raises(ValidationError, match="IP addresses or CIDR"):
        Settings(TRACEFORGE_SECRET_KEY=TEST_SECRET_KEY, TRACEFORGE_API_KEY_PEPPER=TEST_API_KEY_PEPPER,  # type: ignore[call-arg]
                 TRACEFORGE_TRUSTED_PROXIES=value)


# --- through the API -------------------------------------------------------------------------------------------


@pytest.fixture
def behind_proxy(app: FastAPI, settings: Settings) -> Iterator[None]:
    original = app.state.settings
    app.state.settings = settings.model_copy(update={"trusted_proxies": PROXY})
    yield
    app.state.settings = original


def fail_login(client: Any, forwarded: str | None = None, email: str = "victim@example.com") -> int:
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return client.post(LOGIN, json={"email": email, "password": "wrong password"}, headers=headers).status_code


def test_spoofed_forwarded_header_cannot_reset_the_budget_without_a_trusted_proxy(make_client: Any, behind_proxy: None) -> None:
    attacker = make_client(ip="203.0.113.9")  # not the trusted proxy
    codes = [fail_login(attacker, forwarded=f"198.51.100.{i}") for i in range(LOGIN_MAX_FAILURES + 1)]
    assert codes[:LOGIN_MAX_FAILURES] == [401] * LOGIN_MAX_FAILURES
    assert codes[-1] == 429


def test_spoofed_first_entry_through_the_proxy_cannot_reset_the_budget(make_client: Any, behind_proxy: None) -> None:
    proxy = make_client(ip=PROXY)
    # Each request: a fresh fake address typed by the client, then the real one appended by the proxy.
    codes = [fail_login(proxy, forwarded=f"1.2.3.{i}, 198.51.100.7") for i in range(LOGIN_MAX_FAILURES + 1)]
    assert codes[-1] == 429


def test_visitors_behind_the_proxy_get_separate_budgets(make_client: Any, behind_proxy: None) -> None:
    proxy = make_client(ip=PROXY)
    for _ in range(LOGIN_MAX_FAILURES):
        assert fail_login(proxy, forwarded="198.51.100.7") == 401
    assert fail_login(proxy, forwarded="198.51.100.7") == 429
    # A different visitor through the same proxy is not locked out by the first one.
    assert fail_login(proxy, forwarded="198.51.100.8") == 401


def test_default_settings_still_share_one_budget_per_peer(make_client: Any) -> None:
    proxy = make_client(ip=PROXY)  # no trusted proxies configured
    for i in range(LOGIN_MAX_FAILURES):
        assert fail_login(proxy, forwarded=f"198.51.100.{i}") == 401
    assert fail_login(proxy, forwarded="198.51.100.99") == 429


def test_https_origin_is_accepted_only_through_a_trusted_proxy(make_client: Any, behind_proxy: None) -> None:
    body = {"email": "ada@example.com", "password": "correct horse battery staple", "display_name": "Ada"}
    tls_headers = {"Origin": "https://testserver", "X-Forwarded-Proto": "https"}
    spoofed = make_client(ip="203.0.113.9").post("/api/v1/auth/register", json=body, headers=tls_headers)
    assert spoofed.status_code == 403
    proxied = make_client(ip=PROXY).post("/api/v1/auth/register", json=body, headers=tls_headers)
    assert proxied.status_code == 201, proxied.text

"""The error contract every GitLoom SDK shares: the API's own code when the
body carries the envelope, and a code a caller can branch on when it does not."""

import http.server
import socket
import threading
import traceback

import httpx
import pytest

from gitloom import Gitloom, GitloomError

FORBIDDEN_403 = ("The API key was not accepted (403 Forbidden) — check the API key "
                 "(GITLOOM_API_KEY, or the key passed to the client), or whether it has been revoked.")
UNAUTHORIZED_401 = ("No API key was accepted (401 Unauthorized) — check the API key "
                    "(GITLOOM_API_KEY, or the key passed to the client).")


def refusal(status, **response):
    gl = Gitloom("k", namespace="ns",
                 transport=httpx.MockTransport(lambda r: httpx.Response(status, **response)))
    with pytest.raises(GitloomError) as e:
        gl.recall("x")
    assert e.value.status == status
    return e.value


def test_the_gateway_refusing_a_key_is_unauthorized():
    e = refusal(403, json={"message": "Forbidden"})
    assert (e.code, e.message) == ("unauthorized", FORBIDDEN_403)

    e = refusal(401, json={"message": "Unauthorized"})
    assert (e.code, e.message) == ("unauthorized", UNAUTHORIZED_401)


def test_an_enveloped_refusal_keeps_its_own_code():
    e = refusal(403, json={"error": {"code": "scope_denied", "message": "this key cannot read ns"}})
    assert (e.code, e.message) == ("scope_denied", "this key cannot read ns")

    e = refusal(429, json={"error": {"code": "quota_exceeded"}})
    assert (e.code, e.message) == ("quota_exceeded", "Too Many Requests")


def test_the_flat_error_shape_keeps_its_text():
    e = refusal(400, json={"error": "q is required"})
    assert (e.code, e.message) == ("http_400", "q is required")


def test_any_other_error_is_named_by_its_status():
    e = refusal(500, text="upstream exploded\n")
    assert (e.code, e.message) == ("http_500", "upstream exploded")

    e = refusal(502, json={"message": "Internal server error"})
    assert (e.code, e.message) == ("http_502", "Internal server error")

    e = refusal(500, text="x" * 1000)
    assert len(e.message) == 300


@pytest.mark.parametrize("body", [b"", b"  \n\t", b"null"])
def test_a_body_with_nothing_to_say_reads_the_status_text(body):
    e = refusal(503, content=body, headers={"content-type": "application/json"})
    assert (e.code, e.message) == ("http_503", "Service Unavailable")


@pytest.mark.parametrize("body", [b"[1, 2]", b'"oops"', b"42"])
def test_other_json_that_is_not_an_object_reads_as_text(body):
    e = refusal(500, content=body, headers={"content-type": "application/json"})
    assert (e.code, e.message) == ("http_500", body.decode())


def test_retry_after_is_exposed_on_a_429_and_never_retried():
    calls = []

    def limited(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "7"},
                              json={"error": {"code": "rate_limited", "message": "slow down"}})

    gl = Gitloom("k", transport=httpx.MockTransport(limited))
    with pytest.raises(GitloomError) as e:
        gl.recall("x")
    assert (e.value.code, e.value.retry_after) == ("rate_limited", 7)
    assert len(calls) == 1

    assert refusal(429, json={"error": {"code": "rate_limited", "message": "slow down"}}).retry_after is None
    assert refusal(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, json={}).retry_after is None
    assert refusal(503, headers={"Retry-After": "7"}, json={}).retry_after is None


@pytest.mark.parametrize("key", [None, "", "  ", "\t\n"])
def test_a_missing_key_is_refused_before_any_request(monkeypatch, key):
    monkeypatch.delenv("GITLOOM_API_KEY", raising=False)
    with pytest.raises(GitloomError) as e:
        Gitloom(key)
    assert (e.value.code, e.value.status) == ("missing_api_key", 0)
    assert e.value.message == "No API key. Pass api_key= or set GITLOOM_API_KEY."


def test_the_key_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("GITLOOM_API_KEY", " ")
    with pytest.raises(GitloomError):
        Gitloom()
    monkeypatch.setenv("GITLOOM_API_KEY", "gl_from_env")
    assert Gitloom().api_key == "gl_from_env"


def test_a_transport_failure_is_a_network_error():
    failure = httpx.ConnectError("connection refused")

    def fail(request):
        raise failure

    with pytest.raises(GitloomError) as e:
        Gitloom("k", transport=httpx.MockTransport(fail)).recall("x")
    assert (e.value.code, e.value.status, e.value.message) == ("network_error", 0, "connection refused")
    assert e.value.__cause__ is failure


def test_a_timeout_is_its_own_code():
    failure = httpx.ReadTimeout("timed out")

    def slow(request):
        raise failure

    with pytest.raises(GitloomError) as e:
        Gitloom("k", timeout=2.5, transport=httpx.MockTransport(slow)).recall("x")
    assert (e.value.code, e.value.status) == ("timeout", 0)
    assert e.value.message == "Request timed out after 2.5s"
    assert e.value.__cause__ is failure


PROBE = "gl_test_LEAKPROBE_9x7q"


class _Forbidden(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"message":"Forbidden"}'
        self.send_response(403)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _caught(base_url, timeout=5.0):
    gl = Gitloom(PROBE, base_url=base_url, timeout=timeout)
    try:
        gl.recall("x", tags=["probe"])
    except GitloomError as e:
        return e
    raise AssertionError("the request did not fail")


def _assert_no_key(e):
    chain, seen = [], e
    while seen is not None and seen not in chain:
        chain.append(seen)
        seen = seen.__cause__ or seen.__context__
    texts = [e.message, "".join(traceback.format_exception(type(e), e, e.__traceback__))]
    for x in chain:
        texts += [str(x), repr(x), repr(vars(x)), repr(x.args)]
        if isinstance(x, httpx.RequestError):
            texts += [repr(x.request), repr(x.request.headers), str(x.request.url)]
    assert not [t for t in texts if PROBE in t]
    return chain


def test_the_key_never_reaches_an_error():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Forbidden)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        e = _caught(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
        server.server_close()
    assert e.code == "unauthorized"
    _assert_no_key(e)

    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    e = _caught(f"http://127.0.0.1:{port}")
    assert e.code == "network_error"
    assert len(_assert_no_key(e)) > 1

    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)
    try:
        e = _caught(f"http://127.0.0.1:{silent.getsockname()[1]}", timeout=0.3)
    finally:
        silent.close()
    assert e.code == "timeout"
    assert len(_assert_no_key(e)) > 1

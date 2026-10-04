"""The error contract every GitLoom SDK shares: the API's own code when the
body carries the envelope, and a code a caller can branch on when it does not."""

import httpx
import pytest

from gitloom import Gitloom, GitloomError


def refusal(status, **response):
    gl = Gitloom("k", namespace="ns",
                 transport=httpx.MockTransport(lambda r: httpx.Response(status, **response)))
    with pytest.raises(GitloomError) as e:
        gl.recall("x")
    assert e.value.status == status
    return e.value


def test_the_gateway_refusing_a_key_is_unauthorized():
    e = refusal(403, json={"message": "Forbidden"})
    assert e.code == "unauthorized"
    assert e.message == ("The API key was not accepted (403 Forbidden) — check GITLOOM_API_KEY, "
                         "or whether the key has been revoked.")

    e = refusal(401, json={"message": "Unauthorized"})
    assert e.code == "unauthorized"
    assert e.message == "No API key was accepted (401 Unauthorized) — check GITLOOM_API_KEY."


def test_an_enveloped_refusal_keeps_its_own_code():
    e = refusal(403, json={"error": {"code": "scope_denied", "message": "this key cannot read ns"}})
    assert (e.code, e.message) == ("scope_denied", "this key cannot read ns")

    e = refusal(429, json={"error": {"code": "quota_exceeded"}})
    assert (e.code, e.message) == ("quota_exceeded", "Too Many Requests")


def test_any_other_error_is_named_by_its_status():
    e = refusal(500, text="upstream exploded\n")
    assert (e.code, e.message) == ("http_500", "upstream exploded")

    e = refusal(502, json={"message": "Internal server error"})
    assert (e.code, e.message) == ("http_502", "Internal server error")

    e = refusal(503, content=b"")
    assert (e.code, e.message) == ("http_503", "Service Unavailable")

    e = refusal(500, text="x" * 1000)
    assert len(e.message) == 300


@pytest.mark.parametrize("body", [b"null", b"[1, 2]", b'"oops"'])
def test_a_json_body_that_is_not_an_object_reads_as_text(body):
    e = refusal(500, content=body, headers={"content-type": "application/json"})
    assert (e.code, e.message) == ("http_500", body.decode())


def test_a_missing_key_is_refused_at_construction(monkeypatch):
    monkeypatch.delenv("GITLOOM_API_KEY", raising=False)
    for make in (lambda: Gitloom(), lambda: Gitloom(""), lambda: Gitloom("  ")):
        with pytest.raises(GitloomError) as e:
            make()
        assert (e.value.code, e.value.status) == ("missing_api_key", 0)
        assert e.value.message == "No API key. Pass api_key= or set GITLOOM_API_KEY."

    monkeypatch.setenv("GITLOOM_API_KEY", "")
    with pytest.raises(GitloomError):
        Gitloom()

    monkeypatch.setenv("GITLOOM_API_KEY", "gl_from_env")
    assert Gitloom().api_key == "gl_from_env"


@pytest.mark.parametrize("failure", [
    httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("timed out"),
])
def test_a_transport_failure_is_a_network_error(failure):
    def fail(request):
        raise failure

    gl = Gitloom("k", transport=httpx.MockTransport(fail))
    with pytest.raises(GitloomError) as e:
        gl.recall("x")
    assert (e.value.code, e.value.status) == ("network_error", 0)
    assert e.value.message == str(failure)
    assert e.value.__cause__ is failure

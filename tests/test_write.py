"""The direct memory primitives and the tags and times a write carries,
checked by what the client puts on the wire."""

import datetime as dt
import json

import httpx
import pytest

from gitloom import Gitloom, GitloomError

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class Recorder:
    def __init__(self, reply=None, status=200):
        self.reply = {"status": "accepted"} if reply is None else reply
        self.status = status
        self.requests = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.reply)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def body(self):
        return json.loads(self.last.content) if self.last.content else None

    def query(self) -> str:
        return self.last.url.query.decode()


def recorder(reply=None, status=200):
    r = Recorder(reply, status)
    return r, Gitloom("k", namespace="ns", transport=httpx.MockTransport(r.handle))


def test_write_sends_memories_not_messages():
    r, gl = recorder()
    gl.write([{
        "path": "facts/people/maya.md", "content": "Maya rides a bicycle.",
        "tags": ["people", "#launch"], "confidence": 0.8,
        "occurred_at": dt.datetime(2026, 7, 19, 9, 30, tzinfo=IST),
        "cues": ["how does Maya get around"],
    }])
    assert (r.last.method, r.last.url.path) == ("POST", "/v1/memories")
    body = r.body()
    assert "memories" in body and "messages" not in body
    assert body["namespace"] == "ns"
    m = body["memories"][0]
    assert m["tags"] == ["people", "#launch"]
    assert m["occurred_at"] == int(dt.datetime(2026, 7, 19, 4, 0, tzinfo=dt.timezone.utc).timestamp())
    assert m["confidence"] == 0.8


def test_write_reads_every_kind_of_time():
    r, gl = recorder()
    naive = dt.datetime(2026, 7, 19, 4, 0)
    gl.write([
        {"path": "facts/a.md", "content": "a", "occurred_at": naive},
        {"path": "facts/b.md", "content": "b", "occurred_at": dt.date(2026, 7, 19)},
        {"path": "facts/c.md", "content": "c", "occurred_at": 1784433600},
        {"path": "facts/d.md", "content": "d", "occurred_at": 1784433600.9},
        {"path": "facts/e.md", "content": "e", "occurred_at": "2026-07-19T09:30:00"},
        {"path": "facts/f.md", "content": "f", "occurred_at": dt.datetime(1965, 4, 1, 12, tzinfo=dt.timezone.utc)},
        {"path": "facts/g.md", "content": "g"},
    ], timezone="Asia/Kolkata")
    body = r.body()
    got = [m.get("occurred_at") for m in body["memories"]]
    assert got == [
        1784433600,  # naive is UTC
        "2026-07-19",
        1784433600,
        1784433600,  # floored
        "2026-07-19T09:30:00",  # read in timezone by the server
        "1965-04-01T12:00:00+00:00",  # before 1973 epoch seconds have too few digits
        None,
    ]
    assert body["timezone"] == "Asia/Kolkata"
    assert "occurred_at" not in body["memories"][6]


def test_write_refuses_bad_paths_before_sending():
    r, gl = recorder()
    with pytest.raises(ValueError, match="must end in .md"):
        gl.write([{"path": "facts/ok.md", "content": "x"}, {"path": "facts/not-markdown", "content": "y"}])
    assert r.requests == []


def test_write_nothing_is_a_no_op():
    r, gl = recorder()
    assert gl.write([]) is None
    assert r.requests == []


def test_write_refuses_a_time_it_cannot_send():
    r, gl = recorder()
    with pytest.raises(TypeError):
        gl.write([{"path": "facts/a.md", "content": "a", "occurred_at": True}])
    assert r.requests == []


def test_write_keeps_the_deprecated_date():
    r, gl = recorder()
    with pytest.warns(DeprecationWarning):
        gl.write([{"path": "facts/a.md", "content": "a", "date": "2026-07-19"}])
    assert r.body()["memories"][0]["date"] == "2026-07-19"


def test_remember_sends_tags_and_when_it_happened():
    r, gl = recorder()
    gl.remember(
        [{"role": "user", "content": "We shipped the launch."}],
        tags=["#launch", "team a"],
        occurred_at=dt.datetime(2026, 10, 1, 18, 0, tzinfo=IST),
        timezone="Asia/Kolkata",
        session_id="s1",
    )
    body = r.body()
    assert "messages" in body and "memories" not in body
    assert body["tags"] == ["#launch", "team a"]
    assert body["occurred_at"] == int(dt.datetime(2026, 10, 1, 12, 30, tzinfo=dt.timezone.utc).timestamp())
    assert body["timezone"] == "Asia/Kolkata"

    gl.remember([{"role": "user", "content": "x"}], occurred_at=dt.date(2026, 10, 1))
    assert r.body()["occurred_at"] == "2026-10-01"


def test_remember_leaves_unset_fields_off_the_wire():
    r, gl = recorder()
    gl.remember([{"role": "user", "content": "x"}])
    assert set(r.body()) == {"namespace", "messages"}


def test_remember_keeps_the_deprecated_date():
    r, gl = recorder()
    with pytest.warns(DeprecationWarning):
        gl.remember([{"role": "user", "content": "x"}], date="2026-10-01")
    assert r.body()["date"] == "2026-10-01"
    assert "occurred_at" not in r.body()


def test_get_reads_by_path_and_encodes_a_section():
    r, gl = recorder({"namespace": "ns", "path": "facts/people/maya.md#bike",
                      "content": "Maya rides a bicycle.", "tags": ["people"], "confidence": 0.8})
    m = gl.get("facts/people/maya.md#bike")
    assert (r.last.method, r.last.url.path) == ("GET", "/v1/memories")
    assert "path=facts%2Fpeople%2Fmaya.md%23bike" in r.query()
    assert r.last.url.params["namespace"] == "ns"
    assert m["content"] == "Maya rides a bicycle." and m["confidence"] == 0.8


def test_get_of_a_missing_memory_raises():
    _, gl = recorder({"error": {"code": "not_found", "message": "no memory at \"facts/x.md\""}}, status=404)
    with pytest.raises(GitloomError) as e:
        gl.get("facts/x.md")
    assert (e.value.code, e.value.status) == ("not_found", 404)


def test_forget_uses_the_query_string_not_a_body():
    r, gl = recorder()
    gl.forget(["facts/a.md", "facts/b.md"], namespace="other")
    assert r.last.method == "DELETE" and r.last.url.path == "/v1/memories"
    assert r.last.content == b""
    assert dict(r.last.url.params) == {"path": "facts/a.md,facts/b.md", "namespace": "other"}

    gl.forget("facts/c.md")
    assert r.last.url.params["path"] == "facts/c.md"

    n = len(r.requests)
    gl.forget([])
    assert len(r.requests) == n


def test_tree_and_topics_carry_their_filters():
    r, gl = recorder({"namespace": "ns", "depth": 3, "tree": {"path": "facts", "children": []}, "millis": 1})
    res = gl.tree(path="facts", depth=3)
    assert r.last.url.path == "/v1/tree"
    assert dict(r.last.url.params) == {"namespace": "ns", "path": "facts", "depth": "3"}
    assert res["tree"]["path"] == "facts"

    r, gl = recorder({"namespace": "ns", "topics": [
        {"path": "facts/databases", "name": "databases", "tier": "facts", "parent": "facts",
         "depth": 1, "memories": 4},
    ], "millis": 1})
    res = gl.topics(tier="facts", prefix="facts", like="databas", max_depth=2, min_files=2, limit=50)
    assert r.last.url.path == "/v1/topics"
    assert dict(r.last.url.params) == {
        "namespace": "ns", "tier": "facts", "prefix": "facts", "like": "databas",
        "max_depth": "2", "min_files": "2", "limit": "50",
    }
    assert res["topics"][0]["memories"] == 4


def test_graph_sends_its_limit_and_returns_nodes_and_edges():
    r, gl = recorder({"namespace": "ns", "nodes": [{"path": "facts/a.md", "tier": "facts", "kind": "file"}],
                      "edges": [{"src": "facts/a.md", "dst": "facts/b.md", "label": "spouse"}],
                      "truncated": True, "millis": 2})
    res = gl.graph(limit=100)
    assert r.last.url.path == "/v1/graph"
    assert dict(r.last.url.params) == {"namespace": "ns", "limit": "100"}
    assert res["edges"][0]["label"] == "spouse" and res["truncated"] is True


def test_navigation_leaves_defaults_off_the_wire():
    r, gl = recorder({})
    assert gl.tree() == {}
    assert gl.topics(limit=0) == {"topics": []}
    assert gl.graph(limit=0) == {"nodes": [], "edges": []}
    assert all(dict(req.url.params) == {"namespace": "ns"} for req in r.requests)

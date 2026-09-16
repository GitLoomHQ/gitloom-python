"""Tests against a fake API that keeps the server's invariants: sequences
advance, messages are never deleted, compactions are recorded not applied."""

import json

import httpx
import pytest

from gitloom import Gitloom, GitloomError, image_data


class FakeAPI:
    def __init__(self):
        self.messages = []
        self.compactions = []
        self.uploads = []
        self.title = ""
        self.next_seq = 0
        self.branch = "main"
        self.retrieve_params = []
        self.skill_params = []
        self.terms = []
        self.forgotten = []
        self.skills = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        body = json.loads(request.content) if request.content else {}
        ok = lambda v: httpx.Response(200, json=v)

        if p == "/v1/media" and request.method == "POST":
            self.uploads.append(body["content_type"])
            return ok({"id": f"med-{len(self.uploads)}", "bytes": 42})
        if p == "/v1/retrieve":
            self.retrieve_params.append(dict(request.url.params))
            if request.url.params.get("mode") in ("summary", "agentic"):
                return ok({"namespace": "ns", "mode": request.url.params["mode"],
                           "answer": "They prefer Python.", "model": "haiku",
                           "memories": [{"path": "facts/a.md", "content": "the user prefers Python",
                                         "score": 0.9, "matched": ["cue"]}], "millis": 9})
            return ok({"namespace": "ns", "mode": "raw", "memories": [
                {"path": "facts/a.md", "tier": "facts", "content": "the user prefers Python",
                 "score": 0.9, "matched": ["lexical", "cue"]}
            ], "candidates": 2, "filtered_out": 1, "millis": 3})
        if p == "/v1/vocab" and request.method == "POST":
            self.terms += body["terms"]
            return ok({"id": "j1", "namespace": "ns", "status": "accepted"})
        if p == "/v1/vocab" and request.method == "DELETE":
            self.forgotten += request.url.params["term"].split(",")
            return ok({"id": "j2", "namespace": "ns", "status": "accepted"})
        if p == "/v1/vocab":
            word = request.url.params.get("word")
            if word:
                if word == "k8s":
                    return ok({"namespace": "ns", "word": word, "found": True,
                               "term": {"term": "kubernetes", "aliases": ["k8s"]}})
                return ok({"namespace": "ns", "word": word, "found": False})
            return ok({"namespace": "ns", "terms": [{"term": "kubernetes", "aliases": ["k8s"]}]})
        if p == "/v1/skills" and request.method == "POST":
            self.skills += body["skills"]
            return ok({"id": "j3", "namespace": "ns", "status": "accepted",
                       "paths": ["skills/ops/deploy.md"]})
        if p == "/v1/skills":
            self.skill_params.append(dict(request.url.params))
            return ok({"namespace": "ns", "skills": [
                {"path": "skills/ops/deploy.md", "name": "Deploy", "content": "Run make deploy.",
                 "score": 0.8, "matched": ["cue"]}
            ]})
        if p == "/v1/conversations" and request.method == "POST":
            return ok({"branch": "main", "next_seq": self.next_seq})
        if p.endswith("/messages") and request.method == "POST":
            for m in body["messages"]:
                m["seq"] = self.next_seq
                m["branch"] = body["branch"]
                self.messages.append(m)
                self.next_seq += 1
            return ok({"next_seq": self.next_seq, "written": len(body["messages"])})
        if p.endswith("/compact"):
            self.compactions.append(body)
            return ok({"compacted": True, "summary": "server summary" if body.get("auto") else body.get("summary")})
        if p.endswith("/edit"):
            seq = body["seq"]
            name = f"main-{seq}"
            msg = dict(body["message"], seq=seq, branch=name)
            self.messages.append(msg)
            self.branch = name
            return ok({"branch": name, "next_seq": seq + 1})
        if "/messages/" in p and request.method == "PATCH":
            seq = int(p.rsplit("/", 1)[1])
            for m in self.messages:
                if m["seq"] == seq and m["branch"] == body.get("branch", self.branch):
                    m["content"] = body["content"]
            return ok({"updated": True})
        if request.method == "PATCH":
            self.title = body["title"]
            return ok({"title": self.title})
        if p == "/v1/quota-limited":
            return httpx.Response(429, json={"error": {"code": "quota_exceeded", "message": "limit reached"}})
        # load
        branch = request.url.params.get("branch") or self.branch
        visible = [m for m in self.messages if m["branch"] == branch]
        return ok({"branch": branch, "title": self.title, "next_seq": self.next_seq, "messages": visible})


@pytest.fixture()
def api():
    return FakeAPI()


@pytest.fixture()
def client(api):
    return Gitloom("gl_test_key", transport=httpx.MockTransport(api.handle), namespace="ns")


def test_append_uploads_data_parts_and_stores_references(api, client):
    conv = client.conversation("c1", model="gpt-4o")
    conv.append({
        "role": "user",
        "content": [
            {"type": "text", "text": "look at this"},
            image_data("aGVsbG8=", "image/png"),
        ],
    })
    assert api.uploads == ["image/png"]
    stored = api.messages[0]
    assert stored["parts"][1]["media_id"] == "med-1"
    assert "data" not in stored["parts"][1]
    # Flattened text travels alongside so ingestion needs no parser.
    assert stored["content"] == "look at this"


def test_cadence_compaction_fires_with_tokens_to_spare(api, client):
    conv = client.conversation(
        "c1", model="claude-sonnet-5", compact_every=2,
        summarize=lambda evicted: "summarized",
    )
    for i in range(3):
        conv.append([
            {"role": "user", "content": f"q{i}"},
            {"role": "assistant", "content": f"a{i}"},
        ])
    assert api.compactions, "the cadence never compacted; nothing would reach memory"


def test_reported_usage_beats_the_estimator(api, client):
    conv = client.conversation(
        "c1", model="gpt-4o", max_tokens=10_000, compact_at=0.5, compact_every=0,
        summarize=lambda evicted: "summarized",
    )
    conv.append(
        [{"role": "user", "content": "short"}, {"role": "assistant", "content": "also short"}],
        usage={"prompt_tokens": 9_000, "completion_tokens": 500},
    )
    conv.append({"role": "user", "content": "tiny"})
    assert api.compactions, "9k reported tokens against a 5k threshold did not compact"


def test_usage_accepts_provider_objects(api, client):
    class OpenAIUsage:
        prompt_tokens = 9_000
        completion_tokens = 500

    conv = client.conversation(
        "c1", model="gpt-4o", max_tokens=10_000, compact_at=0.5, compact_every=0,
        summarize=lambda evicted: "s",
    )
    conv.append([{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], usage=OpenAIUsage())
    conv.append({"role": "user", "content": "z"})
    assert api.compactions


def test_edit_forks_and_original_survives(api, client):
    conv = client.conversation("c1", model="gpt-4o")
    conv.append([
        {"role": "user", "content": "original"},
        {"role": "assistant", "content": "reply"},
    ])
    conv.edit(0, {"role": "user", "content": "edited"})
    assert conv.branch != "main"
    originals = [m for m in api.messages if m["branch"] == "main" and m["seq"] == 0]
    assert originals[0]["content"] == "original"


def test_edit_in_place_rewrites_without_forking(api, client):
    conv = client.conversation("c1", model="gpt-4o")
    conv.append({"role": "user", "content": "my key is sk-123"})
    conv.edit_in_place(0, "my key is [redacted]")
    assert conv.branch == "main"
    assert api.messages[0]["content"] == "my key is [redacted]"
    assert conv.messages()[0]["content"] == "my key is [redacted]"


def test_with_context_queries_memory(api, client):
    conv = client.conversation("c1", model="gpt-4o")
    ctx = conv.with_context("what language do I prefer?")
    assert ctx and ctx["role"] == "system" and "prefers Python" in ctx["content"]
    off = client.conversation("c2", model="gpt-4o", memory="off")
    assert off.with_context("anything") is None


def test_api_errors_carry_their_code(client):
    with pytest.raises(GitloomError) as e:
        client._request("GET", "/v1/quota-limited")
    assert e.value.code == "quota_exceeded"
    assert e.value.status == 429


def test_titles_round_trip(api, client):
    conv = client.conversation("c1", model="gpt-4o")
    conv.set_title("Camera shopping")
    assert api.title == "Camera shopping"
    again = client.load_conversation("c1")
    assert again.title == "Camera shopping"


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fake_openai(seen):
    def create(**kwargs):
        seen.append(kwargs)
        return _Obj(
            choices=[_Obj(message=_Obj(content=f"reply {len(seen)}"))],
            usage=_Obj(prompt_tokens=10, completion_tokens=5),
        )
    return _Obj(chat=_Obj(completions=_Obj(create=create)))


def test_wrap_is_a_drop_in(api, client):
    import gitloom as gl

    seen = []
    openai = gl.wrap(_fake_openai(seen), client)

    # First call: only the new message, plus the one extra field.
    openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "I like Go"}],
        conversation="conv-1",
    )
    assert [m["role"] for m in api.messages] == ["user", "assistant"]

    # Second call: the wrapper supplies the earlier turns itself.
    openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "what do I like?"}],
        conversation="conv-1",
    )
    sent = seen[1]["messages"]
    texts = [f"{m['role']}:{m['content']}" for m in sent]
    assert "user:I like Go" in texts
    assert "assistant:reply 1" in texts
    assert texts[-1] == "user:what do I like?"
    assert "conversation" not in seen[1]
    # Memory context injected as background.
    assert any("prefers Python" in t for t in texts)


def test_wrap_passes_plain_calls_through(api, client):
    import gitloom as gl

    seen = []
    openai = gl.wrap(_fake_openai(seen), client)
    openai.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "x"}])
    assert len(seen) == 1 and "conversation" not in seen[0]
    assert api.messages == []  # nothing stored without a conversation id


def test_server_side_compaction(api, client):
    conv = client.conversation(
        "c1", model="claude-sonnet-5", compact_every=1, summarize="server",
    )
    conv.append([{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}])
    conv.append([{"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}])
    autos = [c for c in api.compactions if c.get("auto")]
    assert autos, "server compaction never asked the server"
    assert "summary" not in autos[0]
    assert "server summary" in conv.messages()[0]["content"]


def test_added_features_live_on_the_wrapped_client(api, client):
    import gitloom as gl

    seen = []
    openai = gl.wrap(_fake_openai(seen), client)
    openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "original"}],
        conversation="conv-1",
    )
    # Branch/edit/title on the SAME managed conversation the wrapper uses.
    conv = openai.gitloom.conversation("conv-1")
    conv.edit(0, {"role": "user", "content": "edited"})
    assert conv.branch != "main"
    conv.set_title("My chat")
    assert api.title == "My chat"

    # The next completion continues from the edited branch: the wrapper and
    # the features facade share state.
    openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "continue"}],
        conversation="conv-1",
    )
    texts = [f"{m['role']}:{m['content']}" for m in seen[-1]["messages"]]
    assert "user:edited" in texts
    assert not any(t == "user:original" for t in texts)


def test_recall_returns_memories_and_sends_every_filter():
    api = FakeAPI()
    gl = Gitloom("k", namespace="ns", transport=httpx.MockTransport(api.handle))

    res = gl.recall(
        "what do they like",
        tiers=["facts", "skills"],
        paths=["facts/events", "incidents"],
        tags=["pref"],
        tags_all=["a", "b"],
        since="2026-01-01",
        until="2026-06-30",
        min_score=0.4,
        context=False,
        detail="full",
        limit=5,
    )
    assert res["memories"][0]["content"] == "the user prefers Python"
    assert res["memories"][0]["matched"] == ["lexical", "cue"]
    assert res["filtered_out"] == 1

    sent = api.retrieve_params[0]
    assert sent["tiers"] == "facts,skills"
    assert sent["paths"] == "facts/events,incidents"
    assert sent["tags_all"] == "a,b"
    assert sent["since"] == "2026-01-01"
    assert sent["min_score"] == "0.4"
    assert sent["context"] == "0"
    assert sent["detail"] == "full"


def test_recall_leaves_defaults_off_the_wire():
    api = FakeAPI()
    gl = Gitloom("k", namespace="ns", transport=httpx.MockTransport(api.handle))
    gl.recall("x")
    assert set(api.retrieve_params[0]) == {"q", "namespace"}


def test_context_reads_the_whole_memory():
    api = FakeAPI()
    gl = Gitloom("k", namespace="ns", transport=httpx.MockTransport(api.handle))
    msg = gl.context("what do they like")
    assert msg["role"] == "system"
    assert "the user prefers Python" in msg["content"]


def test_answer_asks_for_a_summary_and_agentic_on_request():
    api = FakeAPI()
    gl = Gitloom("k", namespace="ns", transport=httpx.MockTransport(api.handle))

    res = gl.answer("what do they like")
    assert res["answer"] == "They prefer Python."
    assert api.retrieve_params[0]["mode"] == "summary"

    gl.answer("what do they like", agentic=True)
    assert api.retrieve_params[1]["mode"] == "agentic"


def test_answer_refuses_to_return_nothing_silently():
    api = FakeAPI()

    def no_answer(request):
        return httpx.Response(200, json={"namespace": "ns", "mode": "summary", "memories": []})

    gl = Gitloom("k", namespace="ns", transport=httpx.MockTransport(no_answer))
    with pytest.raises(GitloomError) as e:
        gl.answer("x")
    assert e.value.code == "no_answer"


def test_vocab_round_trip():
    api = FakeAPI()
    gl = Gitloom("k", namespace="ns", transport=httpx.MockTransport(api.handle))

    gl.vocab.learn([{"term": "kubernetes", "aliases": ["k8s"], "definition": "Orchestration."}])
    assert api.terms[0]["term"] == "kubernetes"

    assert gl.vocab.list()[0]["term"] == "kubernetes"
    assert gl.vocab.lookup("k8s")["term"] == "kubernetes"
    assert gl.vocab.lookup("zzz") is None

    gl.vocab.forget(["kubernetes", "postgres"])
    assert api.forgotten == ["kubernetes", "postgres"]


def test_skills_store_and_find():
    api = FakeAPI()
    gl = Gitloom("k", namespace="ns", transport=httpx.MockTransport(api.handle))

    stored = gl.skills.store([{"name": "Deploy", "topic": "ops", "content": "Run make deploy."}])
    assert stored["paths"] == ["skills/ops/deploy.md"]
    assert api.skills[0]["name"] == "Deploy"

    found = gl.skills.find("ship a release", paths=["ops"], limit=3)
    assert found[0]["name"] == "Deploy"
    assert api.skill_params[0]["q"] == "ship a release"
    assert api.skill_params[0]["paths"] == "ops"

    gl.skills.list()
    assert "q" not in api.skill_params[1]

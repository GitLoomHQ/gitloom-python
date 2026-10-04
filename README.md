# gitloom-sdk

Python SDK for [GitLoom](https://gitloom.cloud) — a **drop-in beside the OpenAI
and Anthropic SDKs**. Wrap the client you already use; your call sites stay
exactly as they are, one field richer, and the conversation manages itself:
rolling context window, memory retrieval, storage, compaction, titles.

```bash
pip install gitloom-sdk
```

## Drop-in

```python
import gitloom
from openai import OpenAI

memory = gitloom.Gitloom()                 # reads GITLOOM_API_KEY
openai = gitloom.wrap(OpenAI(), memory)    # ← the only setup

res = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "What camera do I own?"}],
    conversation="chat-42",                # ← the only change per call
)
print(res.choices[0].message.content)
```

That's the whole loop. Behind that one call:

- the stored conversation supplies the earlier turns — you pass **only the new
  message**, never append anything;
- memory is retrieved for the user's message and injected as background;
- both turns are stored with the response's **real token usage**;
- compaction runs on cadence (default every 5 exchanges) or window pressure,
  and every compaction feeds the summarized turns to memory ingestion;
- untitled conversations get a title automatically at ingestion.

Anthropic clients (`client.messages.create`) wrap identically — system content
moves to the `system` parameter, usage's `input/output` spelling is understood.
Calls without `conversation=` pass through completely untouched.

Configure the conversations the wrapper opens:

```python
openai = gitloom.wrap(OpenAI(), memory,
                      summarize="server",       # GitLoom's model compacts…
                      # summarize=my_function,  # …or yours, locally
                      compact_every=5,
                      namespace=user_id)
```

## Added features, on the same client

Everything the provider SDK doesn't have lives under `.gitloom`:

```python
conv = openai.gitloom.conversation("chat-42")

conv.rewind(6)                                                # fork after seq 6
conv.edit(4, {"role": "user", "content": "ask differently"})  # fork at same seq
conv.edit_in_place(4, "[redacted]")                           # destroy the original (PII)
conv.set_title("Camera shopping")
conv.branches()
```

These act on the **same managed conversation** the completions flow through —
a rewind here is what the next `create(..., conversation="chat-42")` continues
from.

## Recall, filtered and answered

```python
memory = openai.gitloom
memory.remember([{"role": "user", "content": "I moved to Pune."}])

# Ranked memories, no model call. Milliseconds.
res = memory.recall(
    "what camera do I own",
    tiers=["facts"],            # facts | incidents | rules | skills
    paths=["facts/gear"],       # any directories
    tags=["camera"],
    since="2026-01-01",
    min_score=0.3,
    limit=8,
)
for m in res["memories"]:
    print(round(m["score"], 2), m["path"], m["matched"], m["content"])

# One text answer from a fast model over that retrieval …
print(memory.answer("what camera do I own")["answer"])
# … or let a stronger model search the memory itself with tools.
agentic = memory.answer("which trip had the longest flight", agentic=True)
print(agentic["answer"], agentic["trace"])
```

Each entry is one whole memory, not a scattering of its sections, and its
score is calibrated in `[0, 1]` — comparable across queries, so `min_score`
means the same thing every time. `detail="full"` adds git history with the
last diff, labelled relation snippets and cues.

`answer` is metered as a chat, not a read, and raises rather than handing back
an empty string when the model finds nothing to say.

### Tags, and when it happened

```python
memory.remember(
    [{"role": "user", "content": "We shipped the beta to the first ten teams."}],
    tags=["#launch", "beta"],         # on every memory drawn from it
    occurred_at="2026-09-30 18:00",   # when it happened, not when you sent it…
    timezone="Asia/Kolkata",          # …read in this zone
)
```

`occurred_at` keeps backfilled history at its real dates. It takes an aware
`datetime` (a naive one is read as UTC), a `date` for that calendar day, epoch
seconds, or a string: RFC 3339, `YYYY-MM-DD`, or a datetime without an offset,
read in `timezone`. `date=` still works, deprecated.

Tags are trimmed and lowercased; they hold letters, digits, spaces and
`- _ . : / # @`, up to 32 tags of 64 characters. A bad one raises
`GitloomError` with code `invalid_tag`, naming the field.

### Listing without a question

```python
from datetime import date

res = memory.recall(tags=["#launch"], since=date(2026, 9, 1), time_field="occurred")
for m in res["memories"]:
    print(m["occurred_at"], m["path"], m["user_tags"])
```

Without a query, a filter (`tags`, `tags_all`, `since`, `until`, `tiers` or
`paths`) says what to list, and every match comes back newest first by
`time_field` (`occurred`, `created`, or `updated` by default), scored 1. With
neither, `recall` raises `ValueError` before sending anything. A listing is raw
retrieval: `answer` and `rank` need a query. `since` and `until` take what
`occurred_at` takes, and a date-only `until` includes that whole day in `tz`.

Every memory carries `created_at`, `updated_at`, `occurred_at` and
`expires_at` as aware UTC datetimes (None when unknown); `occurred_precision`,
`day` when only the date is known (held as noon UTC) or `instant`;
`occurred_source`, one of `user`, `extracted`, `said` or `written`; and
`user_tags`, the tags you set, which `tags` lists first. The `created` and
`updated` strings remain, deprecated.

### The lane path

`rank` retrieves on the lane path: lexical, cue, body, graph and time lanes each
search on their own, over the curated memories and the conversation turns, and
the time lane reads dates in the question ("last month", "in May"). `fused`
orders what they find by lane score; `jev` has a ranking model order it, and
sets `rank_fallback` when it answers in lane order instead.

```python
res = memory.recall("when did I stake the tomatoes", rank="fused", max_chars=8000)
for m in res["memories"]:
    print(m.get("store"), m.get("said"), m.get("excerpted"), m["content"])

print(memory.answer("what did I plant after the storm", rank="jev", model="sonnet")["answer"])
```

Each memory then says which `store` it came from (`memory`, or a word-for-word
conversation `turn`) and the days it was `said`. `max_chars` caps the memory
content returned: a memory that does not fit is cut to its opening sentence and
the sentences matching the question, and marked `excerpted`. `model` picks the
model that reads the memories in `summary` or `agentic` mode.

## Direct memory

When you already know what a memory says and where it belongs (a migration, or
an agent filing its own conclusion), store it as written, with no extraction:

```python
from datetime import date

memory.write([
    {"path": "facts/people/maya.md", "content": "Maya rides a bicycle to work.",
     "tags": ["people"], "occurred_at": date(2026, 7, 19),
     "cues": ["how does Maya get around"]},
])

# Seconds later, once the write has landed:
memory.get("facts/people/maya.md")["content"]   # read what a recall hit names
memory.tree(path="facts", depth=3)              # tier → topic → file → sections
memory.topics(like="databas")                   # check before inventing a topic
memory.graph(limit=200)                         # nodes, edges, truncated
memory.forget(["facts/people/maya.md"])
```

Paths sit under `facts/`, `incidents/`, `rules/` or `skills/` and end in `.md`,
checked before anything is sent. Send writes in batches: one call is one
commit.

## Vocabulary and skills

```python
# Teach abbreviations and domain terms. A recall for "k8s" then also finds
# memories written "kubernetes", and the definition comes back as `defined`.
memory.vocab.learn([
    {"term": "kubernetes", "aliases": ["k8s", "kube"],
     "definition": "Container orchestration."},
])
memory.vocab.lookup("k8s")          # → {"term": "kubernetes", "aliases": [...]}
memory.vocab.list(like="kube")
memory.vocab.forget(["kubernetes"])

# Store how things are done; find the skill that fits a task.
memory.skills.store([
    {"name": "Deploy to production", "topic": "ops",
     "description": "Ship a release.",
     "content": "## Steps\n1. Tag the release.\n2. `make deploy ENV=prod`",
     "triggers": ["how do I ship a release", "deploy to prod"]},
])
skill = memory.skills.find("release the new build")[0]
```

Skills are memories under the `skills/` tier, so `recall(..., tiers=["skills"])`
reaches them too.

## Multimodal

```python
from gitloom import image_data, text_part

openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": [
        text_part("what's in this photo?"),
        image_data(b64, "image/png"),   # uploaded transparently; stored by reference
    ]}],
    conversation="chat-42",
)
```

## Docs

https://docs.gitloom.cloud/documentation/python

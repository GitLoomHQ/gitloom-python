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

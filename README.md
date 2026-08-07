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

Direct memory, when you want it:

```python
openai.gitloom.remember([{"role": "user", "content": "I moved to Pune."}])
res = openai.gitloom.recall("where do I live?")
# every hit: scores.arms, provenance (git history + diff), relations with snippets
```

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

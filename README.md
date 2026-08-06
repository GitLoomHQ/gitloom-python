# gitloom-sdk

Python SDK for [GitLoom](https://gitloom.cloud) — conversations that cannot
outgrow their context window, backed by a memory the model can consult.

Sits **beside** the OpenAI and Anthropic SDKs, not in place of them: messages
go in and come out in the provider's own shape, and their responses' `usage`
objects time compaction.

```bash
pip install gitloom-sdk
```

```python
import gitloom
memory = gitloom.Gitloom()   # reads GITLOOM_API_KEY
```

## The loop

```python
from openai import OpenAI

openai = OpenAI()
conv = memory.conversation("chat-42", model="gpt-4o", namespace=user_id,
                           summarize=my_summarizer)

def say(text: str) -> str:
    user = {"role": "user", "content": text}
    context = conv.with_context(text)                       # memory, retrieved
    messages = ([context] if context else []) + conv.for_model() + [user]
    res = openai.chat.completions.create(model="gpt-4o", messages=messages)
    reply = res.choices[0].message
    conv.append([user, {"role": "assistant", "content": reply.content}],
                usage=res.usage)                            # real token counts
    return reply.content
```

Anthropic works identically — `usage=res.usage` accepts either SDK's object
(`prompt/completion` or `input/output` spelling) or a plain dict.

- The window never overflows: `for_model()` is always inside the model's
  budget, oldest turns evicted first.
- Compaction runs on a cadence (default every 5 exchanges) or when the window
  fills, summarized **locally** by your `summarize` function — GitLoom never
  sees the conversation to compact it. Each compaction hands the evicted turns
  to memory ingestion, so the flattened detail stays recallable.
- Conversations with no title get one automatically at ingestion.

## Multimodal

```python
from gitloom import image_data, text_part

conv.append({"role": "user", "content": [
    text_part("what's in this photo?"),
    image_data(b64, "image/png"),      # uploaded transparently; stored by reference
]})
```

## Branching, edits, rewind

```python
conv.rewind(6)                          # fork after seq 6, switch to it
conv.edit(4, {"role": "user", "content": "ask differently"})   # fork at same seq
conv.edit_in_place(4, "[redacted]")     # destroy the original, for PII
conv.set_title("Camera shopping")
```

## Memory, directly

```python
memory.remember([{"role": "user", "content": "I moved to Pune."}])
res = memory.recall("where do I live?")
for hit in res["hits"]:
    print(hit["snippet"], hit["scores"]["arms"], hit["provenance"]["when"])
```

Every hit carries its evidence — per-arm scores, git history with the last
diff, labelled relation snippets — the same shape every GitLoom surface
returns.

## Docs

https://gitloom.cloud/docs/sdk-python.html

# Changelog

## 0.4.1 — 2026-09-16

- **`openai.gitloom` forwards the whole memory surface.** It already forwarded
  `recall` and `remember`; `answer`, `vocab` and `skills` were left behind when
  they were added, so reaching them meant going through `openai.gitloom.memory`
  with nothing saying so.
- `vocab` and `skills` are built once per client rather than on every access.


## 0.4.0 — 2026-09-16

- **Recall returns memories.** `res["hits"]` becomes `res["memories"]`, and
  each entry is one whole memory with its `content` rather than a scattering
  of its sections. `matched` names the arms that found it, `sections` the
  headings that matched, `via` what pulled in a neighbour.
- **Scores mean something.** `score` is calibrated in `[0, 1]` and comparable
  across queries, replacing a fused rank that only ordered one response.
  `scores["coverage"]` says how much of the query a memory accounted for.
- **`recall` takes filters** — `tiers`, `paths`, `tags`, `tags_all`, `since`,
  `until`, `min_score`, `context`, `detail`, `include_expired` — applied
  inside every retrieval arm server-side rather than after the fact.
- **`answer(query)`** returns one text answer: a fast model over a retrieval,
  or with `agentic=True` a stronger model that searches the memory itself with
  tools and returns its `trace`. Raises `GitloomError("no_answer")` rather
  than returning an empty string. Both meter as chats.
- **`memory.vocab`** — `learn`, `list`, `lookup`, `forget`. A learned alias
  makes a query for any surface form find memories written with another;
  matched definitions come back as `defined`.
- **`memory.skills`** — `store` and `find`, stored as memories under the
  `skills/` tier.
- `candidates`, `filtered_out` and `timings` report what retrieval did.


## 0.3.0 — 2026-08-08

- **Added features on the wrapped client.** `openai.gitloom.conversation(id)`
  exposes rewind/edit/redaction/titles/branches on the same managed
  conversation the completions flow through; `openai.gitloom.recall/remember`
  for direct memory. The provider surface stays untouched beside it.
- Documentation leads with the drop-in only; the manual append loop is gone.


## 0.2.0 — 2026-08-08

- **Drop-in mode.** `gitloom.wrap(OpenAI(), memory)` — call sites stay the
  provider SDK's, one `conversation="id"` field richer. Only the new messages
  are passed; the stored conversation supplies the window, memory the context,
  both turns stored with the response's usage, compaction on cadence.
  Anthropic-shaped clients wrapped too.
- **Server-side compaction.** `summarize="server"` hands summarization to
  GitLoom's own model; a local function remains the private-by-default choice.

## 0.1.0 — 2026-08-08

- Conversations with a rolling window, usage-timed compaction, branching,
  edits, redaction, titles, multimodal media, and evidenced memory recall.

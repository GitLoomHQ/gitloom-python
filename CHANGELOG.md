# Changelog

## 0.5.0 — Unreleased

- **Direct memory, at parity with the Go SDK.** `write(memories)` stores
  already-formed memories without extraction; `get(path)` reads one back,
  `forget(paths)` deletes, `tree()` and `topics()` show what is in a
  namespace, `graph()` how its memories relate. A path not ending in `.md` is
  refused before sending. All of them are on `openai.gitloom` too.
- **Tags and times on writes.** `remember()` takes `tags` (on every memory
  extracted), `occurred_at` and `timezone`; `write()` memories take `tags` and
  `occurred_at`. A `datetime` is sent as epoch seconds (a naive one is read as
  UTC), a `date` as `YYYY-MM-DD`, a number floored to whole seconds, a string
  as given. `date=` still works, with a `DeprecationWarning`.
- **Recall lists by filter alone.** `recall()` and `context()` need no query
  when `tags`, `tags_all`, `since`, `until`, `tiers` or `paths` says what to
  list. Given neither, they raise `GitloomError` with code `missing_query`
  and status 0 before sending; it is also a `ValueError`. New `time_field`
  (`occurred`, `created`, `updated`) and `tz`.
- **Times on memories.** `created_at`, `updated_at`, `occurred_at` and
  `expires_at` are aware UTC datetimes, None when unknown, beside
  `occurred_source`, `occurred_precision` and `user_tags`. The `created` and
  `updated` strings are deprecated.
- **`get()` reads like `recall()`.** Its `created_at`, `updated_at`,
  `occurred_at` and `expires_at` are aware UTC datetimes too. A time sent as
  RFC 3339 reads into the same datetime as one sent as epoch seconds.
- **One error contract across the SDKs.** An error the API sends in its
  `{"error": {"code", "message"}}` envelope keeps its code and message, and
  the older flat `{"error": "..."}` keeps its text. An empty, blank or JSON
  `null` body reads as the HTTP status text; other JSON that is not an object
  reads as its text, and no longer crashes the error handling. A failed
  connection raises `network_error` and a timeout `timeout`, both status 0,
  with the httpx exception as the cause. No error, string form or cause
  carries the API key.
- **`retry_after`.** A 429 whose `Retry-After` holds whole seconds sets
  `GitloomError.retry_after`; it is None otherwise. The SDK never retries on
  its own.
- **Breaking:** an error without the envelope had code `http_error`; it is
  now `http_<status>`, e.g. `http_500`, with the body's `message`, its text,
  or the HTTP status text as the message.
- **Breaking:** a 401 or 403 without the envelope, the gateway refusing a
  key, is now `unauthorized`, with a message pointing at the API key. It was
  `http_error`, with the gateway's `Forbidden` or `Unauthorized`.
- **Breaking:** `Gitloom()` without a key now raises `missing_api_key`
  immediately, at construction, so code that builds the client at import
  time, before loading dotenv, will raise. A key that is empty or blank after
  trimming counts as none. It used to build, then fail on the first request
  with an httpx `LocalProtocolError`.
- **Breaking:** the key, from the argument or `GITLOOM_API_KEY`, is trimmed,
  and one with whitespace or control characters inside raises
  `invalid_api_key` at construction, without the key in its message. httpx
  used to refuse it on the first request with the key in the error text.
- **Breaking:** transport failures raise `GitloomError` (`network_error` or
  `timeout`) rather than the httpx exception, which is now its `__cause__`.
- **Breaking:** a memory's `created_at`, `updated_at`, `occurred_at` and
  `expires_at` are datetimes, and its `tags` and `user_tags` are `[]` when the
  server sends null. 0.4.x passed the server's integers and nulls through.
- **Breaking:** a naive `datetime` passed to `since` or `until` is read as
  UTC; it used to be sent without an offset and read in `tz`, else UTC.
- **Changed:** query values encode a space as `%20` rather than `+`.
- **`recall()` and `answer()` take `rank`, `max_chars` and `model`.**
  `rank="fused"` or `rank="jev"` retrieves on the lane path, which also
  reaches conversation turns and the dates in a question; `max_chars` caps the
  memory content returned; `model="haiku"` or `model="sonnet"` picks the
  reader in `summary` or `agentic` mode. None is sent unless set, so existing
  calls are unchanged.
- **Lane-path fields.** Memories carry `store`, `said` and `excerpted`, and
  `matched` can name `time`; the result carries `rank` and `rank_fallback`,
  and `timings` the lane path's `embed_ms`, `lanes_ms`, `rank_ms` and
  per-lane `lane`.


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

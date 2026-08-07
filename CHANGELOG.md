# Changelog

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

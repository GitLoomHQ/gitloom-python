# Changelog

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

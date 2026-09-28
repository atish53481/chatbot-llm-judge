# Chrome Sidebar LLM Judge

Judge any chatbot's answers with [DeepEval](https://github.com/confident-ai/deepeval)
metrics from a Chrome side panel. A local FastAPI backend scores the answers with a
separate judge model; the extension shows the scores, the trend across runs, and a
chat window for the chatbot being judged.

## Quick start (Windows)

1. Copy `.env.example` to `.env` and set `JUDGE_API_KEY` (a Groq key works with the
   defaults; for a Command Code key also set
   `JUDGE_BASE_URL=https://api.commandcode.ai/provider/v1`).
2. Double-click `run-backend.bat`. The first start creates `.venv` and installs the
   packages (a few minutes); later starts take seconds. The backend listens on
   `http://127.0.0.1:8000`.
3. Load the extension: see [ChatbotExtension/README.md](ChatbotExtension/README.md).

To add a chatbot, capture one real request from its website and paste it as cURL:

1. Open the chatbot's website, press F12 → **Network**, and send it a message.
2. Right-click the chat request → **Copy** → **Copy as cURL (bash)**.
3. In the side panel, open *Add a chatbot* and paste the command. Everything fills in
   by itself:
   - the method, URL, headers and body;
   - the message you sent, which becomes `{{message}}` (where the judge puts each
     golden question), with earlier turns emptied;
   - the **reply path**: the chatbot is asked your question once, and the answer's
     location is found, e.g. `reply` or `choices.0.message.content`; for a stream,
     the path inside each event.
4. Check the reply shown under **Test**, then press **Save chatbot**. If the wrong text
   was picked as the message, correct it and press **Re-detect**.

Captured requests often carry session cookies or CSRF tokens that expire; when the
chatbot starts answering 401/403, capture a fresh request and paste it again.

## Layout

- `backend/` — FastAPI app (`dashboard/app.py`); the chatbot connector (`targets/`:
  replays a request captured as cURL, `curl.py` parses it); the judge model (`judges/judge.py`); golden answers
  (`datasets/goldens.json`); the metric catalog (`metrics_catalog.py`); run history in
  SQLite (`storage.py`, `judge.db`).
- `tests/` — pytest suite.
- `ChatbotExtension/` — Chrome extension (side panel, dashboard tab).
- `docs/superpowers/` — design spec and implementation plan.

## Configuration (.env)

| Variable | Default | Purpose |
|---|---|---|
| `JUDGE_API_KEY` | required | key for the judge model's OpenAI-compatible API |
| `JUDGE_MODEL` | `openai/gpt-oss-120b` | judge model name |
| `JUDGE_BASE_URL` | `https://api.groq.com/openai/v1` | judge API base URL |
| `JUDGE_DB_PATH` | `judge.db` | SQLite file for chatbots and run history |

The judge is never the chatbot being judged.

The judge's API key, model and base URL can also be set in the side panel under
**Judge settings** (with **Test** before saving). Values saved there are stored in
`judge.db`, override `.env`, and the key is never shown again (only its last 4
characters); **Remove saved key** goes back to the `.env` key.

## Metrics

24 DeepEval metrics in six groups (`backend/metrics_catalog.py`):

| Group | Metrics (default threshold) |
|---|---|
| Chatbot · safety | Bias ≤ 0.5, Toxicity ≤ 0.5, PII Leakage ≤ 0.1, No-Prompt-Leak (G-Eval) ≥ 0.7 |
| Chatbot · conversational (multi-turn) | Conversation Completeness ≥ 0.7, Knowledge Retention ≥ 0.7 |
| RAG · retrieval | Contextual Precision ≥ 0.7, Contextual Recall ≥ 0.7, Contextual Relevancy ≥ 0.7 |
| RAG · quality + Chatbot | Faithfulness ≥ 0.8, Answer Relevancy ≥ 0.7, Hallucination ≤ 0.5, Correctness (G-Eval) ≥ 0.7, Summarization ≥ 0.6 |
| RAG · G-Eval | Citation Quality ≥ 0.7, Helpfulness ≥ 0.7 |
| Security · red team (G-Eval) | Prompt Injection, Jailbreak, Encoded Injection, Data Exfiltration, Social Engineering, Domain Misuse, Non-Advice, Role Violation — all ≥ 0.7 |

Scores run from 0 to 1. **≥** metrics are higher-is-better. **≤** metrics (Hallucination,
Bias, Toxicity, PII Leakage) are shown as a violation rate, lower is better; DeepEval 4
scores them 1 = clean, so the app shows 1 − score and passes it the matching minimum.

In the side panel, **Metrics to run** lists every metric with a tick box (a group tick
selects the whole group) and its threshold; **Run judge** runs the ticked ones. Every
metric's threshold is set there too: **Thresholds for** picks a preset
(Local Development / PR / Staging / Production, from the threshold strategy) and
**Thresholds per metric** edits any one of them (Custom). A run passes when its average
score meets the threshold (≥ or ≤); each case still shows its own pass/fail.

- **Multi-turn** metrics play the **Conversation scenarios** (side panel: add, edit, delete,
  reset; shipped ones in `backend/datasets/conversations.default.json`) turn by turn, sending the conversation so far where the chatbot's request carries it
  (a `history` or `messages` list in the captured body).
- **RAG · retrieval** metrics (and Faithfulness, Citation Quality) score the chatbot's own
  retrieved documents, read from the **Retrieved context path** of its response (found
  automatically when present). When the chatbot returns none, they score each golden's
  reference context instead, and every result and case is labelled "golden reference":
  those scores check the reference data, not the chatbot's retriever.
- **No-Prompt-Leak** sends five prompt-extraction attacks; **Summarization** asks the
  chatbot to summarise each golden's context.

Security metrics send the red-team probes in `backend/datasets/security_probes.default.json` — pick **E-commerce** (orders, refunds) or **Generic** (any assistant) under the target's **Security probes**; set the target's **Chatbot role** in the side panel so Domain Misuse, Non-Advice and Role Violation know what the bot is meant to be.

The dashboard header picks the target, **Cases per run** (1 / 3 / 5 / all) and runs every visible card with **Run all visible**. Tiles show chatbot, RAG-context and judge status, **Tokens used** (judge tokens and calls since the backend started), the **Average score** of the shown cards (lower-is-better metrics counted as 1 − score), and pass / fail / pending.

The dashboard shows, per metric, what it **scores on** (question, actual result, expected
result, context, retrieved context, conversation) and the G-Eval criteria; **Details** lists
every case with its question, actual result, expected result, context, score and the
judge's reason (why).

A **Run judge** sweep asks every golden question and scores the answers. To judge a
single answer instead, chat with the chatbot in the dashboard and press **Judge this
answer** on its reply — ad-hoc scores are not saved, so they never enter the trend.

## Golden answers

Each chatbot is judged against one golden set (theme). `generic`, the default, fits
any chatbot. Most of its questions (what can you do, talk to a human, off-topic,
gibberish, an emergency) expect a kind of behaviour rather than facts. Its grounded
goldens (`context_in_prompt: true`) send short made-up facts with the question
("Answer using only the information below"), so Faithfulness, Hallucination,
Summarization and the contextual metrics can score any chatbot on sticking to them. `general_support`
is the online-shop set the ShopEasy sample bot answers; `legacy_store` holds the
answers migrated from the earlier `evals/` suite. For a chatbot's own facts, give
it its own theme and fill it from its docs.
Add, edit or delete answers in the side panel (each row has an **Edit** button). Changes
last until the side panel is loaded again: every fresh load (reopening the panel or
reloading the extension) restores the shipped sets from
`backend/datasets/goldens.default.json`. To change the defaults, edit that file.
A golden set with your own theme name is not shipped, so it is never reset.

Three ways to fill a golden set, all in the side panel: add answers by hand; upload a
document (PDF, TXT, DOCX, MD); or paste the chatbot's help / FAQ page address under
**Generate from URL**. The last two let DeepEval's Synthesizer write the questions,
expected answers and context. A page built with JavaScript has little readable text;
save it as a PDF and upload that instead. To judge a different website's chatbot, give
its target its own theme and fill that set; the shipped ShopEasy goldens only fit
ShopEasy.

## Tests

```bat
.venv\Scripts\python -m pytest
.venv\Scripts\python -m pytest -m smoke
```

The first command runs the offline suite (no judge tokens); the second runs only the
quick wiring checks. To run every metric against the real judge (this spends tokens):

```bat
set RUN_LIVE_JUDGE=1
.venv\Scripts\python -m pytest -m live
```

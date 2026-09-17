# LLM Judge — Chrome extension

Judge any chatbot's answers with DeepEval metrics from the Chrome side panel.
The extension talks only to the local backend at `http://127.0.0.1:8000`.

## Install

1. Start the backend from the project root: `run-backend.bat`
   (first put `JUDGE_API_KEY=<your judge key>` in the project's `.env`).
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**
   and pick this `ChatbotExtension` folder.
3. Click the **LLM Judge** toolbar button. The side panel opens.

## Use

- **Chatbot**: the side panel starts with a built-in *Sample chatbot* (a mock with
  canned answers) so a first run needs no setup. *Add a chatbot* connects:
  - **Command Code** — a real model. Paste your API key and pick a model (the
    endpoint is filled in for you). Default: `z-ai/glm-5.3-flash`.
  - an **OpenAI-compatible API** (base URL + API key + model),
  - a **custom HTTP API** (chat path, message field, and the dotted path to the reply),
  - a **chatbot web page** with no API: open the page in the current tab, save, then
    click the page's message box, its Send button (or press Esc to send with Enter),
    and the area where replies appear. The judge then types each golden question into
    the page and reads the reply.
- **Golden answers**: each chatbot uses a golden set (theme). Add, edit or delete
  question / expected-answer pairs (optional context lines and categories) in the side
  panel — **Edit** loads a row back into the form, **Save changes** writes it back.
- **Judge**: pick a metric (or *All metrics*) and press **Run judge**. The latest scores
  chart updates in the side panel and in the dashboard.
- **Dashboard** (*Open dashboard*): chat with the chatbot, see the latest score per
  metric (bar, radar, or table), the trend across runs, and the last run case by case.
  Every chatbot reply has a **Judge this answer** button: it scores just that answer
  with the selected metric, and nothing is saved to the trend.

## Manual smoke checklist

1. Backend running; side panel shows "Judge ready · <model>" (or the JUDGE_API_KEY hint,
   with **Run judge** disabled and a tooltip).
2. Chatbot list shows "Sample chatbot (sample)"; golden list shows 10 answers.
3. Add a golden answer, see the count rise; edit it (Edit, change the question, Save
   changes); delete it (two clicks), see it go.
4. Run *Answer Relevancy*: a result line appears, then the latest-scores chart.
5. Open the dashboard: the KPI tiles fill in; the chat answers "What is your refund
   window?" with the 7-business-days policy; the case table lists 10 rows.
6. Run the same metric again: the trend chart shows two points; Table views show the
   same numbers.
7. Switch the OS to dark mode: charts redraw with the dark palette.
8. Stop the backend: both views show "Backend not reachable…"; start it again and the
   side panel recovers within 10 seconds.

## Security notes

- The backend accepts requests only from this extension's origin and only on
  `127.0.0.1` / `localhost`.
- API keys for connected chatbots are stored in the local `judge.db` and are never
  sent back to the extension (responses show `***`).
- Site access for a web-page chatbot is requested per site, only when you add one.

## Limits

- The web-page relay reads new text from the reply area you clicked. Pages that
  re-render their whole history or stream very slowly may need a second try.

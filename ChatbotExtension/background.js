// Service worker: opens the side panel from the toolbar button, opens the
// dashboard tab, and carries relay traffic for the content script (a page on
// an https site may not call the http://127.0.0.1 backend itself).
importScripts("lib/api.js");

chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error("LLM Judge: side panel setup failed", error));

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Only this extension's own pages and content scripts may use these.
  if (sender.id !== chrome.runtime.id) return false;

  if (message.type === "OPEN_DASHBOARD") {
    chrome.tabs.create({ url: chrome.runtime.getURL("dashboard/dashboard.html") });
    return false;
  }
  if (message.type === "RELAY_NEXT") {
    api(`/api/relay/next?session_id=${encodeURIComponent(message.sessionId)}`)
      .then((data) => sendResponse({ ok: true, id: data.id, question: data.question }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true; // keeps the channel open for the async reply
  }
  if (message.type === "RELAY_REPLY") {
    api("/api/relay", {
      method: "POST",
      body: { session_id: message.sessionId, question_id: message.questionId, text: message.text },
    })
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  return false;
});

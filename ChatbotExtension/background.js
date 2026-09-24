// Service worker: opens the side panel from the toolbar button and opens the
// dashboard tab.

chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error("LLM Judge: side panel setup failed", error));

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Only this extension's own pages may use these.
  if (sender.id !== chrome.runtime.id) return false;

  if (message.type === "OPEN_DASHBOARD") {
    chrome.tabs.create({ url: chrome.runtime.getURL("dashboard/dashboard.html") });
    return false;
  }
  return false;
});

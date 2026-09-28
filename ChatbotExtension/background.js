// Service worker: opens the side panel from the toolbar button, opens the
// dashboard tab, and tells the user when a run finishes (a notification plus a
// badge on the toolbar icon), so a long run can be left in another tab.

const DASHBOARD_URL = chrome.runtime.getURL("dashboard/dashboard.html");
const BADGE_COLORS = { pass: "#0c7a0c", fail: "#c62828", error: "#b36b00" };

chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error("LLM Judge: side panel setup failed", error));

// Focuses an open dashboard tab, or opens one. getContexts finds this
// extension's own pages without the "tabs" permission.
async function showDashboard() {
  const [page] = await chrome.runtime.getContexts({ contextTypes: ["TAB"], documentUrls: [DASHBOARD_URL] });
  if (page && page.tabId >= 0) {
    await chrome.tabs.update(page.tabId, { active: true });
    await chrome.windows.update(page.windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url: DASHBOARD_URL });
  }
}

// summary: {outcome: "pass" | "fail" | "error", badge, title, message}, built by
// announceRun in lib/ui.js from the run's results.
async function runFinished(summary) {
  await chrome.action.setBadgeText({ text: summary.badge || "" });
  await chrome.action.setBadgeBackgroundColor({ color: BADGE_COLORS[summary.outcome] || BADGE_COLORS.error });
  await chrome.notifications.create(`run-${Date.now()}`, {
    type: "basic",
    iconUrl: chrome.runtime.getURL("icons/icon-128.png"),
    title: summary.title,
    message: summary.message,
    priority: summary.outcome === "pass" ? 0 : 1,
  });
}

chrome.notifications.onClicked.addListener((id) => {
  if (!id.startsWith("run-")) return;
  chrome.notifications.clear(id);
  showDashboard().catch((error) => console.error("LLM Judge: could not open the dashboard", error));
});

chrome.runtime.onMessage.addListener((message, sender) => {
  // Only this extension's own pages may use these.
  if (sender.id !== chrome.runtime.id) return false;

  if (message.type === "OPEN_DASHBOARD") {
    chrome.tabs.create({ url: DASHBOARD_URL });
  } else if (message.type === "RUN_FINISHED" && message.summary) {
    runFinished(message.summary).catch((error) => console.error("LLM Judge: notification failed", error));
  } else if (message.type === "CLEAR_BADGE") {
    chrome.action.setBadgeText({ text: "" });
  }
  return false;
});

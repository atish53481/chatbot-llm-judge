// Background service worker for chatbot extension

// Install handler - set defaults
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({
    chatHistory: [],
    settings: {
      typingSpeed: 800,
      botName: 'AI Assistant'
    }
  });
  console.log('Chatbot extension installed');
});

// Message handler for cross-component communication
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'GET_HISTORY') {
    chrome.storage.local.get(['chatHistory'], (result) => {
      sendResponse({ history: result.chatHistory || [] });
    });
    return true; // async response
  }

  if (request.type === 'CLEAR_HISTORY') {
    chrome.storage.local.set({ chatHistory: [] }, () => {
      sendResponse({ success: true });
    });
    return true;
  }

  if (request.type === 'GET_SETTINGS') {
    chrome.storage.local.get(['settings'], (result) => {
      sendResponse({ settings: result.settings || {} });
    });
    return true;
  }
});

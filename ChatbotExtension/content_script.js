// LLM Judge relay, injected on demand into a chatbot's own tab (side panel ->
// Add a chatbot -> "Chatbot web page"). The user clicks the message box, the
// Send button (or presses Esc to send with Enter), and the reply area. After
// that the script asks the backend, through the extension's service worker,
// for questions; types each one into the page; and relays the reply, tagged
// with the question's id, once the page has stopped changing.
(() => {
  if (window.__llmJudgeRelay) return; // already running in this tab
  window.__llmJudgeRelay = true;

  const SESSION_ID = location.hostname;
  const POLL_MS = 1000;
  const SETTLE_MS = 1500; // a reply counts as finished after this long without DOM changes
  const REPLY_TIMEOUT_MS = 55000; // the backend stops waiting at 60s
  const EDITABLE =
    "textarea, input:not([type]), input[type='text'], input[type='search'], [contenteditable=''], [contenteditable='true']";

  let input = null;
  let sendButton = null;
  let replyArea = null;
  let busy = false;

  const banner = document.createElement("div");
  banner.setAttribute("role", "status");
  Object.assign(banner.style, {
    position: "fixed",
    right: "12px",
    bottom: "12px",
    zIndex: "2147483647",
    maxWidth: "320px",
    padding: "8px 12px",
    borderRadius: "8px",
    background: "#0b0b0b",
    color: "#ffffff",
    font: "13px/1.4 system-ui, sans-serif",
    boxShadow: "0 2px 8px rgba(0, 0, 0, 0.3)",
    pointerEvents: "none",
  });
  document.documentElement.append(banner);

  function say(text) {
    banner.textContent = `LLM Judge: ${text}`;
  }

  function send(message) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage(message, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message });
        } else {
          resolve(response);
        }
      });
    });
  }

  function pick(prompt, allowSkip) {
    say(prompt);
    return new Promise((resolve) => {
      const cleanup = () => {
        document.removeEventListener("click", onClick, true);
        document.removeEventListener("keydown", onKey, true);
      };
      const onClick = (event) => {
        event.preventDefault();
        event.stopImmediatePropagation();
        cleanup();
        resolve(event.target);
      };
      const onKey = (event) => {
        if (allowSkip && event.key === "Escape") {
          cleanup();
          resolve(null);
        }
      };
      document.addEventListener("click", onClick, true);
      document.addEventListener("keydown", onKey, true);
    });
  }

  function asEditable(node) {
    if (!(node instanceof Element)) return null;
    return node.closest(EDITABLE) || node.querySelector(EDITABLE);
  }

  function setText(box, text) {
    box.focus();
    if (box.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(box);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand("insertText", false, text);
      return;
    }
    // Frameworks such as React track the native value setter, not .value.
    const proto =
      box instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function submit(box) {
    if (sendButton && sendButton.isConnected) {
      sendButton.click();
      return;
    }
    const init = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true };
    box.dispatchEvent(new KeyboardEvent("keydown", init));
    box.dispatchEvent(new KeyboardEvent("keypress", init));
    box.dispatchEvent(new KeyboardEvent("keyup", init));
  }

  function newText(before, after, question) {
    // Chat pages append: keep what was added, starting after our own question.
    let added = after.startsWith(before) ? after.slice(before.length) : after;
    const echo = added.lastIndexOf(question);
    if (echo >= 0) added = added.slice(echo + question.length);
    return added.trim();
  }

  function waitForReply(question) {
    const before = replyArea.innerText;
    return new Promise((resolve, reject) => {
      let settle = null;
      let timeout = null;
      const observer = new MutationObserver(() => {
        clearTimeout(settle);
        settle = setTimeout(() => {
          const text = newText(before, replyArea.innerText, question);
          if (!text) return; // only our own question so far: keep waiting
          stop();
          resolve(text);
        }, SETTLE_MS);
      });
      const stop = () => {
        observer.disconnect();
        clearTimeout(settle);
        clearTimeout(timeout);
      };
      timeout = setTimeout(() => {
        stop();
        reject(new Error("the chatbot did not answer in time"));
      }, REPLY_TIMEOUT_MS);
      observer.observe(replyArea, { childList: true, subtree: true, characterData: true });
    });
  }

  async function poll() {
    if (busy) return;
    busy = true;
    try {
      const next = await send({ type: "RELAY_NEXT", sessionId: SESSION_ID });
      if (!next || !next.ok) {
        say(`backend unreachable (${next ? next.error : "no response"})`);
        return;
      }
      if (!next.question) return;
      say(`asking "${next.question.slice(0, 60)}"`);
      const reply = waitForReply(next.question);
      reply.catch(() => {}); // handled below; avoids an unhandled rejection if typing fails
      setText(input, next.question);
      submit(input);
      const text = await reply;
      await send({ type: "RELAY_REPLY", sessionId: SESSION_ID, questionId: next.id, text });
      say("answer sent to the judge; waiting for the next question");
    } catch (error) {
      say(error.message);
    } finally {
      busy = false;
    }
  }

  async function arm() {
    input = asEditable(await pick("click the chatbot's message box"));
    if (!input) {
      say("that is not a text box. Reload the page and add the chatbot again.");
      return;
    }
    const button = await pick("click the chatbot's Send button, or press Esc to send with Enter", true);
    sendButton = button
      ? button.closest("button, [role='button'], input[type='submit']") || button
      : null;
    replyArea = await pick("click the area where the chatbot's replies appear");
    say("ready, waiting for questions from the judge");
    setInterval(poll, POLL_MS);
  }

  arm();
})();

// Popup script for chatbot extension
const chatContainer = document.getElementById('chatContainer');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');

let chatHistory = [];

// Predefined responses for demo chatbot
const responses = {
  'store hours': "We're open Monday through Friday 9 AM to 6 PM, and Saturday 10 AM to 4 PM. We're closed on Sundays.",
  'return': "You can return any unused product within 30 days of purchase with the original receipt. Bring it to any store location or ship it back using our prepaid return label.",
  'student discount': "Yes! Students get 15% off with a valid student ID. The discount applies to regular-priced items, excluding sale items.",
  'payment': "We accept Visa, Mastercard, American Express, Discover, PayPal, and Apple Pay both online and in-store.",
  'shipping': "Standard shipping takes 5-7 business days. Express shipping (2-3 days) and overnight options are also available at checkout.",
  'default': "I'm here to help! You can ask me about store hours, returns, discounts, payment methods, or shipping."
};

// Get bot response based on user input
function getBotResponse(userMessage) {
  const msg = userMessage.toLowerCase();

  for (const [key, response] of Object.entries(responses)) {
    if (key !== 'default' && msg.includes(key)) {
      return response;
    }
  }

  return responses.default;
}

// Add message to chat
function addMessage(text, isUser) {
  // Remove empty state if exists
  const emptyState = chatContainer.querySelector('.empty-state');
  if (emptyState) {
    emptyState.remove();
  }

  const messageDiv = document.createElement('div');
  messageDiv.className = `message ${isUser ? 'user' : 'bot'}`;

  const bubbleDiv = document.createElement('div');
  bubbleDiv.className = 'message-bubble';
  bubbleDiv.textContent = text;

  const timeDiv = document.createElement('div');
  timeDiv.className = 'message-time';
  const now = new Date();
  timeDiv.textContent = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });

  messageDiv.appendChild(bubbleDiv);
  messageDiv.appendChild(timeDiv);
  chatContainer.appendChild(messageDiv);

  // Scroll to bottom
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

// Show typing indicator
function showTyping() {
  const typingDiv = document.createElement('div');
  typingDiv.className = 'typing-indicator';
  typingDiv.id = 'typing';
  typingDiv.innerHTML = '<span></span><span></span><span></span>';
  typingDiv.style.display = 'flex';
  chatContainer.appendChild(typingDiv);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

// Remove typing indicator
function hideTyping() {
  const typing = document.getElementById('typing');
  if (typing) {
    typing.remove();
  }
}

// Send message
async function sendMessage() {
  const message = userInput.value.trim();
  if (!message) return;

  // Add user message
  addMessage(message, true);
  chatHistory.push({ role: 'user', content: message });

  // Clear input
  userInput.value = '';
  sendBtn.disabled = true;

  // Show typing
  showTyping();

  // Simulate delay
  await new Promise(resolve => setTimeout(resolve, 800));

  // Get bot response
  const response = getBotResponse(message);

  // Hide typing and show response
  hideTyping();
  addMessage(response, false);
  chatHistory.push({ role: 'bot', content: response });

  // Save to storage
  chrome.storage.local.set({ chatHistory });

  sendBtn.disabled = false;
  userInput.focus();
}

// Event listeners
sendBtn.addEventListener('click', sendMessage);
userInput.addEventListener('keypress', (e) => {
  if (e.key === 'Enter') {
    sendMessage();
  }
});

// Load chat history on startup
chrome.storage.local.get(['chatHistory'], (result) => {
  if (result.chatHistory && result.chatHistory.length > 0) {
    chatHistory = result.chatHistory;
    result.chatHistory.forEach(msg => {
      addMessage(msg.content, msg.role === 'user');
    });
  }
});

// Focus input
userInput.focus();

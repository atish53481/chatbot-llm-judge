# Chrome Extension Chatbot for DeepEval Testing

Sample chatbot Chrome extension for practicing LLM evaluation with DeepEval framework.

## Installation

1. Open Chrome and go to `chrome://extensions/`
2. Enable "Developer mode" (top right)
3. Click "Load unpacked"
4. Select the `ChatbotExtension` folder

## Features

- **Simple Q&A chatbot** with predefined responses
- **Chat history** persisted in Chrome storage
- **Typing indicator** for realistic interaction
- **Responsive UI** with gradient header
- **5 sample topics**: store hours, returns, discounts, payments, shipping

## Testing with DeepEval

Connect this chatbot to the evaluation system in `../evals/`:

1. Update `conftest.py` to query the extension's responses
2. Run evaluation suite against predefined responses
3. Test metrics: relevancy, faithfulness, hallucination detection

## File Structure

```
ChatbotExtension/
├── manifest.json       # Extension configuration
├── background.js       # Service worker for storage
└── popup/
    ├── popup.html      # Chat UI
    └── popup.js        # Chat logic + responses
```

## Predefined Responses

The chatbot responds to questions about:
- **Store hours**: Mon-Fri 9-6, Sat 10-4, Sun closed
- **Returns**: 30 days, unused, receipt required
- **Student discount**: 15% off with valid ID
- **Payment methods**: Visa, MC, Amex, Discover, PayPal, Apple Pay
- **Shipping**: Standard 5-7 days, Express 2-3 days, Overnight 1 day

## Usage

1. Click extension icon in toolbar
2. Type questions in chat input
3. Press Enter or click Send
4. Chat history persists across sessions

## Integration with DeepEval

Use this chatbot as the "application under test" for the evaluation system. The responses match the golden dataset in `evals/datasets/chatbot_golden.json`.

See main README.md for full DeepEval setup instructions.

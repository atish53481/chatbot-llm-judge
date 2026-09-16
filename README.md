# Chatbot LLM Judge - DeepEval Framework

Evaluation system for chatbot quality using DeepEval metrics. Tests answer relevancy, faithfulness, hallucination detection.

## Structure

```
evals/
├── conftest.py              # pytest fixtures
├── datasets/
│   └── chatbot_golden.json  # versioned test cases
├── test_answer_quality.py   # relevancy + correctness
├── test_faithfulness.py     # faithfulness to context
└── test_safety.py           # hallucination detection
```

## Setup

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...  # for LLM-as-judge
deepeval login                 # optional: dashboard
```

## Run

```bash
deepeval test run evals/ -n 4  # parallel
pytest evals/                  # standard pytest
```

## Metrics

- **AnswerRelevancyMetric** (0.7): Does answer address question?
- **FaithfulnessMetric** (0.85): Does answer match retrieved context?
- **HallucinationMetric** (≤0.15): Invented facts?
- **GEval** (0.7): Custom correctness criteria

## Dataset

30+ cases from real interactions. Add production failures. Version file when baseline changes.

## Integration

Replace mock client in `conftest.py` with your chatbot API:

```python
import requests

class ChatbotClient:
    def answer(self, query: str, context: List[str] = None):
        response = requests.post(
            "http://localhost:8201/chat",
            json={"message": query, "history": []}
        )
        data = response.json()
        return {
            "text": data["reply"],
            "context": context or [],
            "model": data.get("model", "unknown")
        }
```

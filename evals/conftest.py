"""pytest fixtures for chatbot evals"""
import os
from typing import Dict, List

# Mock chatbot client - replace with your actual implementation
class ChatbotClient:
    """Example chatbot under test"""

    def answer(self, query: str, context: List[str] = None) -> Dict[str, any]:
        """
        Query chatbot and return response with metadata

        Returns:
            {
                "text": str,           # actual response
                "context": List[str],  # retrieved context chunks
                "model": str          # model used
            }
        """
        # TODO: Replace with actual chatbot API call
        # Example: POST to http://localhost:8201/chat
        return {
            "text": "This is a mock response. Replace with real chatbot.",
            "context": context or [],
            "model": "mock-model"
        }

# Export client for tests
chatbot = ChatbotClient()

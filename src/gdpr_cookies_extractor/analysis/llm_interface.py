import logging
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# The standard response wrapper remains the same
@dataclass
class LLMResponse:
    """
    A standard wrapper for all LLM API call results.
    """
    success: bool
    data: Optional[Dict[str, Any]]
    error: Optional[str] = None


class AbstractLLMClient(ABC):
    """
    The interface (Abstract Base Class) for all LLM providers.
    
    It guarantees that any provider we use will have an 
    asynchronous 'query_json' method.
    """
    
    @abstractmethod
    async def query_json(self, 
                         user_prompt: str, 
                         system_prompt: str = None) -> LLMResponse:
        """
        Sends a prompt to the LLM and expects a JSON response.
        """
        pass

    def _parse_json_response(self, raw_content: str) -> str:
        """
        A helper utility that can be shared by all implementations
        to robustly parse JSON from the LLM's raw response.
        """
        json_string = None

        # Try to extract JSON from a markdown code block
        match = re.search(r'```json\n(.*?)\n```', raw_content, re.DOTALL)
        if match:
            json_string = match.group(1)
        else:
            match = re.search(r'```\n(.*?)\n```', raw_content, re.DOTALL)
            if match:
                json_string = match.group(1)

        if json_string is None:
            # Fallback to finding the first and last JSON object
            start_index = raw_content.find('{')
            if start_index == -1:
                raise ValueError("No JSON object found in the response.")

            end_index = raw_content.rfind('}') + 1
            if end_index == 0:
                raise ValueError("No JSON object found in the response.")

            json_string = raw_content[start_index:end_index]
        
        # Remove invalid control characters from JSON string
        json_string = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', json_string)
        return json_string
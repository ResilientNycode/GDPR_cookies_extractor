import logging
import json
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
        start_marker = '```json'
        end_marker = '```'
        
        if start_marker in raw_content:
            start_index = raw_content.find(start_marker) + len(start_marker)
            end_index = raw_content.find(end_marker, start_index)
            if end_index == -1:
                end_index = len(raw_content)
            json_string = raw_content[start_index:end_index].strip()
        else:
            start_index = raw_content.find('{')
            end_index = raw_content.rfind('}') + 1
            if start_index == -1 or end_index == 0:
                raise ValueError("No JSON object found in the response.")
            json_string = raw_content[start_index:end_index]
            
        return json_string
import json
import logging
from .llm_interface import AbstractLLMClient, LLMResponse 

logger = logging.getLogger(__name__)

class PrivacyAnalyzer:
    """
    Analyzes privacy policies and cookie data using a provided LLM client.
    """
    
    def __init__(self, llm_client: AbstractLLMClient):
        self.llm_client = llm_client
        logger.info(f"PrivacyAnalyzer initialized with client: {type(llm_client).__name__}")

    async def find_privacy_policy(self, html_content: str, url: str):
        """
        Sends HTML content to the LLM to find the privacy policy URL.
        """
        prompt = f"""
        You are an expert web analysis agent. Your task is to find the URL of the privacy policy page for the given website.
        This page is often linked from the footer, but can also be in a cookie banner, "About Us" section, or other legal notices.
        Analyze the provided HTML content and find the most likely URL for the privacy policy.
        Look for links containing keywords like 'privacy', 'policy', 'GDPR', 'data protection', 'cookie policy', or 'legal notice'.
        The result must be returned as a JSON object.
        
        The HTML content to analyze is below:
        ---
        {html_content}
        ---
        
        The URL of the page is: {url}

        Return your answer as a single JSON object with the following structure:
        {{
          "privacy_policy_url": <string>,
          "reasoning": <string>,
          "confidence_score": <number>
        }}
        Privacy_policy_url must be the complete URL to the privacy page. If no URL is found, set "result_found" to false and "privacy_policy_url" to null.
        In the reasoning field explain what words or other hints you have choosen to pick that privacy_policy_url as privacy page or why you didn't find it.
        In confidence_score field tell me from 0 to 1 how sure you are that the provided privacy_policy_url  is the correct one containing the privacy page.
        Just return the json object, no needs of introduction or other strings in the response. 
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            return {
                "privacy_policy_url": None,
                "reasoning": response.error,
                "confidence_score": 0.0
            }
        
        return response.data

    async def analyze_retention_policy(self, privacy_policy_html: str):
        """
        Analyzes the HTML of a privacy policy page to find data retention information.
        """
        prompt = f"""
        You are an expert in GDPR compliance. Your task is to find and summarize the data retention policy in the provided privacy policy HTML.
        Look for keywords and phrases related to data retention, such as "data retention", "how long we keep your data", "storage period", or "period for which data is stored".
        Extract and summarize the key information about how long personal data is kept and any conditions for its retention.
        
        Privacy Policy HTML content:
        ---
        {privacy_policy_html}
        ---
        
        Return your answer as a single JSON object with the following structure:
        {{
          "retention_policy_summary": <string>,
          "reasoning": <string>
          "confidence_score": <number>
        }}
        If no retention policy is found, set "retention_policy_summary" to null.
        In the reasoning field explain what words or other hints have you had to generate the retention_policy_summary field or why you didn't succeed.  
        In confidence_score field tell me from 0 to 1 how sure you are that the retention_policy_summary contains the correct information requested. 
        Just return the json object, no needs of introduction or other strings in the repsponse.
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            return { 
                    "retention_policy_summary": None, 
                    "reasoning": response.error,
                    "confidence_score": 0.0
            }
        
        return response.data

    async def find_dpo(self, privacy_policy_html: str, url: str):
        """
        Analyzes the HTML of a privacy policy page to find DPO contact information.
        """
        dpo_prompt = f"""
        You are an expert in GDPR compliance and a pure text extractor. 
        
        STRICT RULE: YOU MUST ONLY USE THE TEXT PROVIDED IN THE HTML CONTENT BELOW. DO NOT USE ANY EXTERNAL KNOWLEDGE.
        
        Your task is to find the official contact details for the Data Protection Officer (DPO).
        
        1.  **PRIORITY EXTRACTION**: Search for email addresses containing 'dpo@' first, then 'privacy@', then 'legal@'. Extract the single best email and the main postal address found.
        2.  **SUB-LINK IMPERATIVE**: If the STRICT SUCCESS CONDITION is FALSE, you MUST look for the single most promising **relative URL** sub-link on the page that mentions 'Governance' or 'Contact'.
        
        Privacy Policy HTML content:
        ---
        {privacy_policy_html}
        ---
        
        The URL of the page is: {url}

        Return your answer as a single JSON object with the following structure:
        {{
          "email_address": <string>,  // The best email found (DPO)
          "postal_address": <string>,
          "reasoning": <string>,
          "confidence_score": <number>
        }}
        If you cannot find ANY email address in the provided HTML text, you MUST set the email_address to null and provide a reasoning that indicates the reason why text was not found.
        If you cannot find ANY postal_address address in the provided HTML text, you MUST set the postal_address to null and provide a reasoning that indicates the reason why text was not found.
        In the reasoning field explain what words or other hints have you had to generate the email_address and postal_address fields or why you didn't succeed.  
        In confidence_score field tell me from 0 to 1 how sure you are that the email_address and postal_address contain the correct information requested. 
        Just return the json object, no needs of introduction or other strings in the repsponse.
        """
        
        response = await self.llm_client.query_json(user_prompt=dpo_prompt)
        
        if not response.success:
            return {
                "dpo_found": False, 
                "email_address": None,
                "postal_address": None,
                "sub_link": None, 
                "reasoning": response.error
            }
            
        return response.data

    async def categorize_cookies(self, cookies_data: list):
        """
        Categorizes a list of cookies using the LLM.
        """
        cookies_json_list = json.dumps(cookies_data, indent=2)
        prompt = f"""
        You are an expert in GDPR cookie compliance. Your task is to categorize a list of cookies based on their name and properties.
        Categorize each cookie into one of the following types:
        - "Strictly Necessary": Essential for the website's basic function (e.g., sessions, shopping cart).
        - "Functional": Remembers user choices (e.g., language, preferences).
        - "Analytical": Collects data on user behavior to improve the site (e.g., Google Analytics).
        - "Marketing": Tracks users for advertising and personalization.
        - "Uncategorized": No clear purpose can be determined.
        
        Return a JSON object with the website's cookies categorized into these types.

        Cookies to categorize:
        {cookies_json_list}
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            logger.error(f"Cookie categorization failed: {response.error}")
            return {} # Return empty dict on failure
            
        return response.data
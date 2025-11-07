import json
import logging
from urllib.parse import urljoin, urlparse
from typing import Dict, Any, List, Callable, Coroutine
from .llm_interface import AbstractLLMClient, LLMResponse
from . import scraper
import asyncio
import httpx

# Configure logger
logger = logging.getLogger(__name__)

class PrivacyAnalyzer:
    """
    Analyzes a site to find its Privacy Policy, and THEN analyzes that 
    policy page to find cookie and data deletion information.
    
    This class implements a sequential, dependent search strategy with a more robust
    core analysis logic:
    1.  For each target (e.g., cookie policy), it first analyzes the *text content* of the current page.
    2.  Only if the information is not found in the text, it then searches for promising *links* to follow (fan-out).
    """
    
    def __init__(self, llm_client: AbstractLLMClient, max_hops: int = 3):
        self.llm_client = llm_client
        self.max_hops = max_hops
        logger.info(f"PrivacyAnalyzer initialized with client: {type(llm_client).__name__} and max_hops: {max_hops}")

    # --- PUBLIC METHODS ---

    async def find_privacy_policy(self, browser, site_url: str, user_keywords: List[str] = None) -> Dict[str, Any]:
        """
        Public method to find the privacy policy.
        This is the entry point for the first step of the analysis.
        """
        logger.info(f"--- STEP 1: Finding Privacy Policy, starting from {site_url} ---")
        return await self._find_target_orchestrator(
            browser=browser,
            start_url=site_url,
            extraction_method=self._extract_privacy_policy_info,
            result_key="privacy_policy_url",
            user_keywords=user_keywords
        )

    async def find_cookie_declaration(self, browser, start_url: str, user_keywords: List[str] = None) -> Dict[str, Any]:
        """
        Public method to find the cookie declaration, starting from a given URL (e.g., a privacy policy page).
        """
        logger.info(f"--- Analyzing for Cookie Declaration, starting from {start_url} ---")
        return await self._find_target_orchestrator(
            browser=browser,
            start_url=start_url,
            extraction_method=self._extract_cookie_declaration_info,
            result_key="cookie_declaration_url",
            user_keywords=user_keywords
        )

    async def find_data_deletion_info(self, browser, start_url: str, user_keywords: List[str] = None) -> Dict[str, Any]:
        """
        Public method to find data deletion info, starting from a given URL.
        """
        logger.info(f"--- Analyzing for Data Deletion Info, starting from {start_url} ---")
        return await self._find_target_orchestrator(
            browser=browser,
            start_url=start_url,
            extraction_method=self._extract_data_deletion_info,
            result_key="data_deletion_url",
            user_keywords=user_keywords
        )

    async def categorize_cookies(self, cookies_data: list):
        cookies_json_list = json.dumps(cookies_data, indent=2)
        prompt = f"""
        You are an expert in GDPR compliance and a JSON-only generator.
        Your task is to categorize a list of cookies and provide a brief description for each, based on your general knowledge.

        CATEGORIES DEFINITIONS:
        - "Strictly Necessary": Essential for website function (e.g., session, security, shopping cart).
        - "Functional": Remembers user choices (e.g., language, preferences).
        - "Analytical": Collects data on user behavior (e.g., Google Analytics).
        - "Marketing": Tracks users for advertising.
        - "Uncategorized": Unknown or generic purpose.

        INSTRUCTIONS:
        - Analyze each cookie in the "Input Cookies" list.
        - Based on the cookie's "name" and "domain", categorize it.
        - Create a "description" for each cookie based on your general knowledge (e.g., a cookie named "_ga" is for Google Analytics).
        - CRITICAL RULE: If a cookie's name is generic or unknown (e.g., "uid"), you MUST set its description to "No specific description available." Do NOT invent a purpose.
        - Return a single JSON object with the root key "cookie_categories".
        - The value of "cookie_categories" must be a list of objects (one for each category that contains cookies).
        - Each cookie object in the *output* "cookies" list MUST have this structure:
            - "name": The original cookie name.
            - "domain": The original cookie domain.
            - "description": Your generated description (or "No specific description available.").

        EXAMPLE OF REQUIRED OUTPUT FORMAT:
        {{
          "cookie_categories": [
            {{
              "category_name": "Strictly Necessary",
              "cookies": [
                {{ "name": "sessionid", "domain": "example.com", "description": "No specific description available." }}
              ]
            }},
            {{
              "category_name": "Analytical",
              "cookies": [
                {{ "name": "_ga", "domain": ".example.com", "description": "Google Analytics cookie used to distinguish users." }}
              ]
            }}
          ]
        }}

        INPUT COOKIES TO CATEGORIZE:
        {cookies_json_list}
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            logger.error(f"Cookie categorization failed: {response.error}")
            return {}
            
        return response.data

    # --- CORE LOGIC: ORCHESTRATOR ---

    async def _find_target_orchestrator(
        self,
        browser,
        start_url: str,
        extraction_method: Callable,
        result_key: str,
        user_keywords: List[str] = None
    ) -> Dict[str, Any]:
        """
        Orchestrates the search for a specific target.
        Implements the "Analyze Content -> Then Search Links" logic.
        """
        page = None
        try:
            logger.info(f"Starting search for '{result_key}' on {start_url}...")
            page = await browser.new_page()
            await page.goto(start_url, timeout=90000)
            html_content = await page.content()

            # --- 1. Analyze Content First ("Sniper") ---
            initial_analysis = await extraction_method(html_content, start_url)
            
            # Check if info was found embedded in the text
            if initial_analysis.get("is_embedded"):
                logger.info(f"Found embedded information for '{result_key}' on {start_url}.")
                return initial_analysis

            # Check if a direct URL was found
            if initial_analysis.get(result_key):
                 logger.info(f"Found direct URL for '{result_key}' on {start_url}.")
                 return initial_analysis

            # --- 2. If nothing found, Search Links ("Explorer" / "Worker") ---
            logger.info(f"No direct or embedded info found for '{result_key}'. Analyzing links for fan-out...")
            
            promising_links = initial_analysis.get("promising_links", [])
            if not promising_links:
                logger.warning(f"No promising links identified by the LLM on {start_url}. Falling back to all internal links.")
                internal_links = await scraper.extract_links(page, start_url)
                promising_links = [{"url": link} for link in internal_links]

            candidate_urls = [link.get("url") for link in promising_links if link.get("url")]
            valid_urls_to_scan = await self._validate_candidate_links(candidate_urls)
            
            search_tasks = []
            for i, link_url in enumerate(valid_urls_to_scan):
                if i >= self.max_hops:
                    logger.warning(f"Reached max_hops limit ({self.max_hops}).")
                    break
                
                task_page = await browser.new_page()
                task = asyncio.create_task(self._analyze_sub_page_worker(
                    page=task_page,
                    url=link_url,
                    hop_num=i + 1,
                    extraction_method=extraction_method,
                    result_key=result_key
                ))
                search_tasks.append(task)
            
            if search_tasks:
                results_from_fan_out = await asyncio.gather(*search_tasks)
                successful_results = [res for res in results_from_fan_out if res and (res.get(result_key) or res.get("is_embedded"))]
                if successful_results:
                    best_item = successful_results[0]
                    logger.info(f"Found info for '{result_key}' via fan-out on {best_item.get('source_url')}")
                    return best_item

            logger.info(f"No item for '{result_key}' found after deep search.")
            return {"reasoning": "No relevant information found after deep search.", result_key: None}

        except Exception as e:
            logger.error(f"Error during '{result_key}' search for {start_url}: {e}", exc_info=True)
            return {"reasoning": f"Failed during search: {e}", result_key: None}
        finally:
            if page:
                await page.close()

    async def _analyze_sub_page_worker(self, page, url: str, hop_num: int, extraction_method: Callable, result_key: str) -> Dict[str, Any]:
        """
        Worker for the fan-out. Visits a sub-page and runs the extraction method.
        This is a simple "Sniper" run; it does not fan-out further.
        """
        try:
            logger.info(f"Analyzing '{result_key}' (Fan-out Hop {hop_num}): {url}")
            await page.goto(url, timeout=90000)
            html = await page.content()
            analysis_result = await extraction_method(html, url)

            if analysis_result.get("is_embedded") or analysis_result.get(result_key):
                return analysis_result
            return None
        except Exception as e:
            logger.error(f"Error analyzing sub-page {url}: {e}")
            return None
        finally:
            if page:
                await page.close()

    # --- NEW EXTRACTION METHODS ---

    async def _extract_privacy_policy_info(self, html_content: str, url: str) -> Dict[str, Any]:
        prompt = f"""
        You are a specialized web analysis agent that ONLY returns JSON.
        Your task is to find the Privacy Policy URL from the given HTML content.

        **Analysis Steps:**
        1.  **Check if the current page IS the policy:** Read the HTML text. If the content is clearly a privacy policy, this page's URL is the answer.
        2.  **Find the best link:** If the current page is NOT the policy, scan all `<a>` tags for the most likely link to the privacy policy.
            - Prioritize links in common locations like footers, headers, or dedicated "legal" sections.
            - Keywords to look for in link text or surrounding elements are 'privacy', 'policy', 'data protection', 'GDPR', 'legal', 'terms'.
            - If multiple promising links are found, return all of them in the "promising_links" array.

        **CRITICAL JSON OUTPUT RULES:**
        - You MUST return a single, valid JSON object. NO OTHER TEXT.
        - The JSON object MUST have one of the following structures, and NOTHING ELSE.

        - **Case 1: The policy URL is found (either this page or a link)**
          Return a JSON object with the key "privacy_policy_url".
          `{{"privacy_policy_url": "<URL of the policy>", "is_embedded": <true_if_this_page_is_the_policy_else_false>, "reasoning": "Brief explanation."}}`

        - **Case 2: No clear policy URL is found, but there are potential links**
          Return a JSON object with the key "promising_links".
          `{{"promising_links": [{{"url": "<url1>"}}, {{"url": "<url2>"}}]}}`

        - **Case 3: Nothing relevant is found**
          Return an empty JSON object.
          `{{}}`

        **EXAMPLE 1 (Link Found):**
        `{{"privacy_policy_url": "https://example.com/privacy", "is_embedded": false, "reasoning": "Found a direct link with the text 'Privacy Policy'."}}`

        **EXAMPLE 2 (Page is the Policy):**
        `{{"privacy_policy_url": "{url}", "is_embedded": true, "reasoning": "The content of this page is the privacy policy."}}`
        
        **EXAMPLE 3 (Nothing Found):**
        `{{}}`

        Base URL for context: {url}
        HTML to analyze:
        ---
        {html_content}
        ---
        """
        response = await self.llm_client.query_json(user_prompt=prompt)
        if response.success and response.data:
            if response.data.get("privacy_policy_url"):
                response.data["privacy_policy_url"] = urljoin(url, response.data["privacy_policy_url"])
            if response.data.get("promising_links"):
                for link in response.data["promising_links"]:
                    link["url"] = urljoin(url, link["url"])
        return response.data if response.success else {}

    async def _extract_cookie_declaration_info(self, html_content: str, url: str) -> Dict[str, Any]:
        prompt = f"""
        You are a specialized web analysis agent that ONLY returns JSON.
        Your task is to find information about cookie usage from the given HTML.

        **Analysis Steps:**
        1.  **Check if the current page IS the cookie declaration:** Read the HTML text. If the content clearly explains cookie usage, this page's URL is the answer.
        2.  **Find the best link:** If the current page is NOT the cookie declaration, scan all `<a>` tags for the most likely link to a dedicated "Cookie Policy", "Cookie Declaration", or "Technologies" page.

        **CRITICAL JSON OUTPUT RULES:**
        - You MUST return a single, valid JSON object. NO OTHER TEXT.
        - The JSON object MUST have one of the following structures, and NOTHING ELSE.

        - **Case 1: Cookie information is found embedded in the text**
          Return a JSON object with the key "summary".
          `{{"summary": "<your_summary_of_the_cookie_section>", "is_embedded": true, "source_url": "{url}" }}`

        - **Case 2: A clear link to a cookie page is found**
          Return a JSON object with the key "cookie_declaration_url".
          `{{"cookie_declaration_url": "<found_url>", "is_embedded": false, "source_url": "{url}"}}`

        - **Case 3: No clear cookie information or link is found, but there are potential links**
          Return a JSON object with the key "promising_links".
          `{{"promising_links": [{{"url": "<url1>"}}, {{"url": "<url2>"}}]}}`

        - **Case 4: Nothing relevant is found**
          Return an empty JSON object.
          `{{}}`

        **EXAMPLE 1 (Summary Found):**
        `{{"summary": "This page describes the use of strictly necessary and analytical cookies.", "is_embedded": true, "source_url": "{url}" }}`

        **EXAMPLE 2 (Link Found):**
        `{{"cookie_declaration_url": "https://example.com/cookies", "is_embedded": false, "source_url": "{url}"}}`
        
        **EXAMPLE 3 (Nothing Found):**
        `{{}}`

        Base URL for context: {url}
        HTML to analyze:
        ---
        {html_content}
        ---
        """
        response = await self.llm_client.query_json(user_prompt=prompt)
        if response.success and response.data:
            if response.data.get("cookie_declaration_url"):
                response.data["cookie_declaration_url"] = urljoin(url, response.data["cookie_declaration_url"])
            if response.data.get("promising_links"):
                for link in response.data["promising_links"]:
                    link["url"] = urljoin(url, link["url"])
        return response.data if response.success else {}

    async def _extract_data_deletion_info(self, html_content: str, url: str) -> Dict[str, Any]:
        prompt = f"""
        You are a GDPR expert. Your task is to find how a user can delete their data from the given HTML.

        **Step 1: Analyze Content First**
        Read the text of the HTML. Is there a section that explains the process for data deletion or account closure? It might mention a specific email address or a set of instructions. If you find this information, summarize it.

        **Step 2: Find a Link**
        Only if you did not find clear instructions in the text, scan all `<a>` tags to find a link to a dedicated page like "Delete Account", "Privacy Dashboard", or "Manage Your Data".

        **Instructions for JSON Output:**
        You MUST return a single JSON object.
        - If you found the deletion instructions embedded in the text (Step 1), return:
          `{{"summary": "<your_summary_of_the_deletion_process>", "is_embedded": true, "source_url": "{url}" }}`
        - If you found a clear link to a deletion page (Step 2), return:
          `{{"data_deletion_url": "<found_url>", "is_embedded": false, "source_url": "{url}"}}`
        - If you find neither, but there are some plausible links, return a list of them:
          `{{"promising_links": [{{"url": "<url1>"}}, {{"url": "<url2>"}}]}}`
        - If you find nothing at all, return an empty JSON object.
          `{{}}`

        **EXAMPLE 1 (Summary Found):**
        `{{"summary": "Users can delete their account by navigating to settings and clicking 'Delete Account'.", "is_embedded": true, "source_url": "{url}" }}`

        **EXAMPLE 2 (Link Found):**
        `{{"data_deletion_url": "https://example.com/delete-data", "is_embedded": false, "source_url": "{url}"}}`
        
        **EXAMPLE 3 (Nothing Found):**
        `{{}}`

        Base URL for context: {url}
        HTML to analyze:
        ---
        {html_content}
        ---
        """
        response = await self.llm_client.query_json(user_prompt=prompt)
        if response.success and response.data:
            if response.data.get("data_deletion_url"):
                response.data["data_deletion_url"] = urljoin(url, response.data["data_deletion_url"])
            if response.data.get("promising_links"):
                for link in response.data["promising_links"]:
                    link["url"] = urljoin(url, link["url"])
        return response.data if response.success else {}

    # --- UTILITY METHODS ---

    async def _is_valid_html_url(self, url: str) -> bool:
        if not url or not url.startswith(('http://', 'https://')):
            return False
        
        excluded_extensions = ('.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.pdf', '.xml', '.json', '.zip', '.rar', '.tar', '.gz', '.svg', '.ico', '.webp', '.mp4', '.mp3')
        try:
            path = urlparse(url).path
            if path and path.lower().endswith(excluded_extensions):
                return False
        except Exception:
             return False

        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
                response = await client.head(url, timeout=10, headers=headers)
                if response.status_code >= 400: return False
                content_type = response.headers.get('content-type', '').lower()
                return 'text/html' in content_type
        except (httpx.RequestError, httpx.TimeoutException, Exception):
            return False

    async def _validate_candidate_links(self, links: List[str]) -> List[str]:
        tasks = [self._is_valid_html_url(link) for link in links]
        results = await asyncio.gather(*tasks)
        return [link for link, is_valid in zip(links, results) if is_valid]
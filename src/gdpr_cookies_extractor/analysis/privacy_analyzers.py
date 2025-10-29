import json
import logging
from urllib.parse import urljoin, urlparse
from typing import Dict, Any, List
from .llm_interface import AbstractLLMClient, LLMResponse 
import asyncio

logger = logging.getLogger(__name__)

class PrivacyAnalyzer:
    """
    Analyzes privacy policies and cookie data using a provided LLM client.
    """
    
    def __init__(self, llm_client: AbstractLLMClient, max_hops: int = 3):
        self.llm_client = llm_client
        self.max_hops = max_hops
        logger.info(f"PrivacyAnalyzer initialized with client: {type(llm_client).__name__} and max_hops: {max_hops}")

    async def find_privacy_policy(self, html_content: str, url: str):
        """
        Sends HTML content to the LLM to find the privacy policy URL.
        """
        prompt = f"""
        You are an expert web analysis agent. Your task is to find the URL of the privacy policy page for the given website.
        This page is often linked from the footer, but can also be in a cookie banner, "About Us" section, or other legal notices.
        Analyze the provided HTML content and find the most likely URL for the privacy policy.
        Look for links containing keywords like 'privacy', 'policy', 'GDPR', 'data protection', 'cookie policy', or 'legal notice'.
        
        The HTML content to analyze is below:
        ---
        {html_content}
        ---
        
        The URL of the page is: {url}

        You MUST return a single JSON object and nothing else. Do not include any text or explanation before or after the JSON object.
        Return your answer as a single JSON object with the following structure:
        {{
          "privacy_policy_url": <string>,
          "reasoning": <string>,
          "confidence_score": <number>
        }}
        Privacy_policy_url must be the complete URL to the privacy page. If no URL is found, set "result_found" to false and "privacy_policy_url" to null.
        In the reasoning field explain what words or other hints you have choosen to pick that privacy_policy_url as privacy page or why you didn't find it.
        In confidence_score field tell me from 0 to 1 how sure you are that the provided privacy_policy_url  is the correct one containing the privacy page.
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            return {
                "privacy_policy_url": None,
                "reasoning": response.error,
                "confidence_score": 0.0
            }
        
        return response.data

    async def analyze_retention_policy(self, privacy_policy_html: str, url: str):
        """
        Analyzes the HTML of a privacy policy page to find data retention information.
        """
        prompt = f"""
        You are an expert in GDPR compliance. Your task is to find and summarize the data retention policy in the provided privacy policy HTML.
        Look for keywords and phrases related to data retention, such as "data retention", "how long we keep your data", "storage period", or "period for which data is stored".
        Extract and summarize the key information about how long personal data is kept and any conditions for its retention.
        
        The URL of the page being analyzed is: {url}

        Privacy Policy HTML content:
        ---
        {privacy_policy_html}
        ---
        
        You MUST return a single JSON object and nothing else. Do not include any text or explanation before or after the JSON object.
        Return your answer as a single JSON object with the following structure:
        {{
          "retention_policy_summary": <string>, 
          "reasoning": <string>,
          "confidence_score": <number>,
          "source_url": "{url}"
        }}
        If no retention policy is found, set "retention_policy_summary" to null.
        In the reasoning field explain what words or other hints have you had to generate the retention_policy_summary field or why you didn't succeed.  
        In confidence_score field tell me from 0 to 1 how sure you are that the retention_policy_summary contains the correct information requested. 
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            return {{
                    "retention_policy_summary": None, 
                    "reasoning": response.error,
                    "confidence_score": 0.0,
                    "source_url": url
            }}
        
        return response.data

    async def extract_dpo_info_from_page(self, html_content: str, url: str):
        """
        Analyzes the HTML of a given page to find DPO contact information.
        """
        dpo_prompt = f"""
        You are an expert in GDPR compliance and a pure text extractor.

        **STRICT RULE:** You MUST only use the text provided in the HTML content below. Do not use any external knowledge or web search. Your task is to extract information ONLY from the provided text.

        Your task is to find the official contact details for the Data Protection Officer (DPO) from the HTML content.

        **Primary Goal:** Find the best email address for the DPO. Search for emails containing 'dpo@', 'privacy@', or 'legal@'.
        **Secondary Goal:** Find any main postal address for the DPO.

        The URL of the page being analyzed is: {url}

        Page HTML content:
        ---
        {html_content}
        ---

        You MUST return a single JSON object and nothing else. Do not include any text or explanation before or after the JSON object.
        Return your answer as a single JSON object with the following structure:
        {{
          "email_address": <string> or null,
          "postal_address": <string> or null,
          "reasoning": <string>,
          "confidence_score": <number>,
          "source_url": "{url}"
        }}
        
        - If you find an email, return it in the 'email_address' field.
        - If you find a postal address, return it in the 'postal_address' field.
        - In 'reasoning', briefly explain your findings.
        - In 'confidence_score', provide a score from 0.0 to 1.0 indicating your certainty.
        """
        
        response = await self.llm_client.query_json(user_prompt=dpo_prompt)
        
        if not response.success:
            return {{
                "email_address": None,
                "postal_address": None,
                "reasoning": response.error,
                "source_url": url,
                "confidence_score": 0.0
            }}
            
        return response.data

    async def _get_internal_links(self, page, site_url: str) -> List[str]:
        """
        Helper to extract all internal links from a page.
        """
        links = []
        base_netloc = urlparse(site_url).netloc
        
        for a in await page.query_selector_all('a'):
            try:
                href = await a.get_attribute('href')
                if href:
                    full_url = urljoin(site_url, href)
                    if urlparse(full_url).netloc == base_netloc and '#' not in full_url and not full_url.endswith(('.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.pdf')):
                        links.append(full_url)
            except Exception as e:
                logger.error(f"Could not process link: {e}")
        
        for link in links:
            if not isinstance(link, str):
                logger.error(f"Found a non-string link: {link} of type {type(link)}")

        return list(set(links)) # Return unique links

    async def _analyze_dpo_sub_page_for_fan_out(self, page, url: str, site_url: str, scenario: str, hop_num: int) -> Dict[str, Any]:
        """
        Helper to analyze a sub-page for DPO info during fan-out search.
        """
        try:
            logger.info(f"[{scenario}] Analyzing for DPO (Fan-out Hop {hop_num}): {url}")
            await page.goto(url, timeout=60000)
            html = await page.content()
            dpo_output = await self.extract_dpo_info_from_page(html, url)
            await page.close()
            return dpo_output
        except Exception as e:
            logger.error(f"[{scenario}] Error analyzing DPO sub-page {url}: {e}")
            await page.close()
            return {}

    async def find_dpo(self, browser, site_url: str, scenario: str) -> Dict[str, Any]:
        """
        Navigates the site to find the DPO information page and extracts DPO contact details.
        """
        page = None
        try:
            logger.info(f"[{scenario}] Starting DPO search for: {site_url} using internal link navigation.")
            
            # 1. Initial page load and DPO info extraction
            page = await browser.new_page()
            await page.goto(site_url, timeout=60000)
            html_content = await page.content()
            initial_dpo_output = await self.extract_dpo_info_from_page(html_content, site_url)
            
            final_dpo_output = {}

            if initial_dpo_output.get('email_address'):
                logger.info(f"[{scenario}] DPO found on initial page: {initial_dpo_output.get('email_address')}")
                final_dpo_output = initial_dpo_output
            else:
                logger.info(f"[{scenario}] DPO email not found directly. Starting fan-out search for DPO.")
                
                internal_links = await self._get_internal_links(page, site_url)
                
                promising_keywords = ['dpo', 'data protection', 'governance', 'legal', 'contact', 'privacy']
                promising_links = [
                    link for link in internal_links 
                    if any(keyword in link.lower() for keyword in promising_keywords)
                ]
                
                dpo_search_tasks = []
                for i, link in enumerate(promising_links):
                    if i >= self.max_hops:
                        logger.warning(f"[{scenario}] Reached max_hops limit ({self.max_hops}). Not all promising links will be checked.")
                        break
                    
                    task_page = await browser.new_page()
                    task = asyncio.create_task(self._analyze_dpo_sub_page_for_fan_out(task_page, link, site_url, scenario, i + 1))
                    dpo_search_tasks.append(task)
                
                found_dpos = await asyncio.gather(*dpo_search_tasks)
                
                valid_dpos = [dpo for dpo in found_dpos if dpo and dpo.get('email_address')]
                
                if valid_dpos:
                    final_dpo_output = valid_dpos[0]
                    logger.info(f"[{scenario}] DPO found via fan-out search: {final_dpo_output.get('email_address')}")
                else:
                    final_dpo_output = {"reasoning": "No DPO information found after extensive internal link search.", "source_url": site_url}

            await page.close()
            return final_dpo_output

        except Exception as e:
            logger.error(f"[{scenario}] Error during DPO search for {site_url}: {e}")
            if page:
                await page.close()
            return {"reasoning": f"Failed during DPO search: {e}", "source_url": None}

    async def extract_cookie_declaration_url_from_page(self, html_content: str, url: str):
        """
        Analyzes the HTML of a given page to find the cookie declaration page URL.
        """
        prompt = f"""
        You are an expert web analysis agent. Your task is to find the URL of the cookie declaration or cookie settings page for the given website.
        This URL MUST point to a navigable HTML page, NOT a script file (.js), CSS file (.css), image, or any other non-HTML asset.
        This page is often linked from the privacy policy or a cookie banner.
        Analyze the provided HTML content and find the most likely URL for the cookie declaration page.
        Look for links containing keywords like 'cookie', 'technologies', or 'cookie and other technologies'.
        Examples of INVALID URLs: /static/js/cookie-script.js, /assets/css/styles.css, /images/cookie-icon.png
        
        The HTML content to analyze is below:
        ---
        {html_content}
        ---
        
        The URL of the page is: {url}

        You MUST return a single JSON object and nothing else. Do not include any text or explanation before or after the JSON object.
        Return your answer as a single JSON object with the following structure:
        {{
          "cookie_declaration_url": <string>,
          "reasoning": <string>,
          "confidence_score": <number>
        }}
        cookie_declaration_url must be a full URL not a sub path. If no URL is found, set "cookie_declaration_url" to null.

        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            return {
                "cookie_declaration_url": None,
                "reasoning": response.error,
                "confidence_score": 0.0
            }
        
        return response.data

    async def is_cookie_declaration_page(self, html_content: str, url: str):
        """
        Analyzes the HTML of a given page to determine if it is a cookie declaration page.
        """
        prompt = f"""
        You are an expert web analysis agent. Your task is to determine if the given HTML content represents a cookie declaration page.
        A cookie declaration page lists the cookies used on the site, often grouped by category (e.g., necessary, marketing, analytics).
        Analyze the provided HTML content and determine if it contains a detailed list of cookies.

        The HTML content to analyze is below:
        ---
        {html_content}
        ---

        The URL of the page is: {url}

        You MUST return a single JSON object and nothing else. Do not include any text or explanation before or after the JSON object.
        Return your answer as a single JSON object with the following structure:
        {{
          "is_cookie_declaration": <boolean>,
          "reasoning": <string>,
          "confidence_score": <number>
        }}
        - "is_cookie_declaration": true if the page contains a cookie declaration, false otherwise.
        - "reasoning": Explain your decision.
        - "confidence_score": A score from 0.0 to 1.0 on your certainty.
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            return {
                "is_cookie_declaration": False,
                "reasoning": response.error,
                "confidence_score": 0.0
            }
        
        return response.data

    async def _analyze_cookie_sub_page_for_fan_out(self, page, url: str, site_url: str, scenario: str, hop_num: int) -> Dict[str, Any]:
        """
        Helper to analyze a sub-page for cookie declaration URL during fan-out search.
        """
        try:
            logger.info(f"[{scenario}] Analyzing for cookie declaration (Fan-out Hop {hop_num}): {url}")
            await page.goto(url, timeout=60000)
            html = await page.content()
            analysis = await self.is_cookie_declaration_page(html, url)
            await page.close()
            if analysis.get("is_cookie_declaration") and analysis.get("confidence_score", 0) > 0.7:
                return {
                    "cookie_declaration_url": url,
                    "reasoning": analysis.get("reasoning"),
                    "confidence_score": analysis.get("confidence_score")
                }
            return {}
        except Exception as e:
            logger.error(f"[{scenario}] Error analyzing cookie declaration sub-page {url}: {e}")
            await page.close()
            return {}

    async def find_cookie_declaration_page(self, browser, site_url: str, scenario: str) -> Dict[str, Any]:
        """
        Navigates the site to find the cookie declaration page.
        """
        page = None
        try:
            logger.info(f"[{scenario}] Starting cookie declaration page search for: {site_url} using internal link navigation.")
            
            page = await browser.new_page()
            await page.goto(site_url, timeout=60000)
            html_content = await page.content()
            
            # 1. Check if the current page is the cookie declaration page
            analysis = await self.is_cookie_declaration_page(html_content, site_url)
            if analysis.get("is_cookie_declaration") and analysis.get("confidence_score", 0) > 0.7:
                logger.info(f"[{scenario}] Cookie declaration found on the initial page: {site_url}")
                await page.close()
                return {
                    "cookie_declaration_url": site_url,
                    "reasoning": analysis.get("reasoning"),
                    "confidence_score": analysis.get("confidence_score")
                }

            logger.info(f"[{scenario}] Initial page is not a cookie declaration page. Starting fan-out search.")
            
            internal_links = await self._get_internal_links(page, site_url)
            
            promising_keywords = ['cookie', 'technologies']
            promising_links = [
                link for link in internal_links 
                if any(keyword in link.lower() for keyword in promising_keywords)
            ]
            
            search_tasks = []
            for i, link in enumerate(promising_links):
                if i >= self.max_hops:
                    logger.warning(f"[{scenario}] Reached max_hops limit ({self.max_hops}). Not all promising links will be checked.")
                    break
                
                task_page = await browser.new_page()
                task = asyncio.create_task(self._analyze_cookie_sub_page_for_fan_out(task_page, link, site_url, scenario, i + 1))
                search_tasks.append(task)
            
            found_pages = await asyncio.gather(*search_tasks)
            
            valid_pages = [p for p in found_pages if p and p.get('cookie_declaration_url')]
            
            if valid_pages:
                final_output = valid_pages[0]
                logger.info(f"[{scenario}] Cookie declaration page found via fan-out search: {final_output.get('cookie_declaration_url')}")
            else:
                final_output = {"reasoning": "No cookie declaration page found after extensive internal link search.", "source_url": site_url, "cookie_declaration_url": None}

            await page.close()
            return final_output

        except Exception as e:
            logger.error(f"[{scenario}] Error during cookie declaration page search for {site_url}: {e}")
            if page:
                await page.close()
            return {"reasoning": f"Failed during cookie declaration page search: {e}", "source_url": None, "cookie_declaration_url": None}

    async def categorize_cookies(self, cookies_data: list):
        """
        Categorizes a list of cookies using the LLM.
        """
        cookies_json_list = json.dumps(cookies_data, indent=2)
        # logger.info(f"Cookies to categorize: {cookies_json_list}")
        prompt = f"""
        You are a JSON generator. Your only function is to categorize the provided list of cookies into a JSON object.

        **Input:** A JSON list of cookie objects.
        **Output:** A single JSON object.

        **Instructions:**
        1.  You will be given a JSON list of cookies.
        2.  You MUST categorize every single cookie from the input list.
        3.  The output MUST be a single JSON object.
        4.  The JSON object must have a single root key named "cookie_categories".
        5.  The value of "cookie_categories" must be a list of JSON objects.
        6.  Each object in the list represents a category and MUST have the following two keys:
            - "category_name": A string with the name of the category (e.g., "Strictly Necessary", "Functional", "Analytical", "Marketing", "Uncategorized").
            - "cookies": A list of the input cookies with name and if possible a description of what does the specific cookie.

        **Do NOT include any text, explanation, or markdown before or after the JSON object.**

        **Example of the required output format:**
        {{{{
          "cookie_categories": [
            {{
              "category_name": "Functional",
              "cookies": [
                {{ "name": "name of the cookie", "description": "brief description of what the cookies is used for",  "domain": "domain of the site" }}
              ]
            }}
          ]
        }}}}

        **Input Cookies to categorize:**
        {cookies_json_list}
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            logger.error(f"Cookie categorization failed: {response.error}")
            return {{}} # Return empty dict on failure
            
        return response.data
    

    async def extract_data_deletion_url_from_page(self, html_content: str, url: str):
        """
        Analyzes the HTML of a given page to find the URL of the page
        dedicated to managing or deleting personal data.
        """
        prompt = f"""
        You are an expert in GDPR and web analysis. Your task is to find the link (URL) to the specific page where a user can manage, access, or delete their personal data.
        This URL MUST point to a navigable HTML page, NOT a script file (.js), CSS file (.css), image, or any other non-HTML asset.

        Analyze the provided HTML and search for the most likely link. 
        Look for keywords such as:
        - "delete your data"
        - "close your account"
        - "how to access and control your personal data"
        - "privacy dashboard"
        - "data subject rights"
        - "right to erasure"
        - "manage your data"
        Examples of INVALID URLs: /static/js/delete-script.js, /assets/css/styles.css, /images/delete-icon.png

        The URL of the page being analyzed is: {url}

        HTML content to analyze:
        ---
        {html_content}
        ---
        
        You MUST return a single JSON object and nothing else. Do not include any text or explanation.
        Return your answer as a single JSON object with this structure:
        {{
          "deletion_page_url": <string> or null,
          "reasoning": <string>,
          "confidence_score": <number>,
          "source_url": "{url}"
        }}

        - "deletion_page_url": The full URL that leads to the data management/deletion page.
        - "reasoning": Briefly explain what keywords or hints led you to that URL.
        - "confidence_score": A score from 0.0 to 1.0 on your certainty.
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            return {
                "deletion_page_url": None,
                "reasoning": response.error,
                "confidence_score": 0.0,
                "source_url": url
            }
        
        return response.data

    async def _analyze_deletion_sub_page_for_fan_out(self, page, url: str, site_url: str, scenario: str, hop_num: int) -> Dict[str, Any]:
        """
        Helper to analyze a sub-page for the data deletion URL during fan-out search.
        """
        try:
            logger.info(f"[{scenario}] Analyzing for data deletion (Fan-out Hop {hop_num}): {url}")
            await page.goto(url, timeout=60000)
            html = await page.content()
            deletion_output = await self.extract_data_deletion_url_from_page(html, url)
            await page.close()
            return deletion_output
        except Exception as e:
            logger.error(f"[{scenario}] Error analyzing data deletion sub-page {url}: {e}")
            await page.close()
            return {}

    async def find_data_deletion_page(self, browser, site_url: str, scenario: str) -> Dict[str, Any]:
        """
        Navigates the site to find the data deletion/management page (e.g., Privacy Dashboard).
        'site_url' should be the URL of the main privacy policy page.
        """
        page = None
        try:
            logger.info(f"[{scenario}] Starting data deletion page search for: {site_url}")
            
            page = await browser.new_page()
            await page.goto(site_url, timeout=60000)
            html_content = await page.content()
            initial_output = await self.extract_data_deletion_url_from_page(html_content, site_url)
            
            final_output = {}

            if initial_output.get('deletion_page_url'):
                logger.info(f"[{scenario}] Data deletion page found on initial page: {initial_output.get('deletion_page_url')}")
                final_output = initial_output
            else:
                logger.info(f"[{scenario}] Data deletion page not found. Starting fan-out search.")
                
                internal_links = await self._get_internal_links(page, site_url)
                
                # Keywords to filter links (in the URLs)
                promising_keywords = [
                    'privacy', 'account', 'delete', 'erasure', 'rights', 
                    'manage', 'control', 'dashboard', 'data'
                ]
                promising_links = [
                    link for link in internal_links 
                    if any(keyword in link.lower() for keyword in promising_keywords)
                ]
                
                search_tasks = []
                for i, link in enumerate(promising_links):
                    if i >= self.max_hops:
                        logger.warning(f"[{scenario}] Reached max_hops limit ({self.max_hops}).")
                        break
                    
                    task_page = await browser.new_page()
                    task = asyncio.create_task(self._analyze_deletion_sub_page_for_fan_out(task_page, link, site_url, scenario, i + 1))
                    search_tasks.append(task)
                
                found_pages = await asyncio.gather(*search_tasks)
                
                valid_pages = [p for p in found_pages if p and p.get('deletion_page_url')]
                
                if valid_pages:
                    final_output = valid_pages[0] # Take the first valid result
                    logger.info(f"[{scenario}] Data deletion page found via fan-out search: {final_output.get('deletion_page_url')}")
                else:
                    final_output = {"reasoning": "No data deletion page found after search.", "source_url": site_url}

            await page.close()
            return final_output

        except Exception as e:
            logger.error(f"[{scenario}] Error during data deletion page search for {site_url}: {e}")
            if page:
                await page.close()
            return {"reasoning": f"Failed during data deletion search: {e}", "source_url": None}
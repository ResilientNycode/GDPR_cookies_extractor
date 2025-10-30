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

    # --- Privacy Policy Methods ---
    async def _extract_policy_url_from_html(self, html_content: str, url: str):
        """
        Sends HTML content to the LLM to find the privacy policy URL on a single page.
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

    async def find_privacy_policy(self, page) -> Dict[str, Any]:
        """
        Orchestrates a deep search for the privacy policy URL.
        It starts with an initial analysis of the given page and, if no policy is found,
        it searches through internal links for promising leads.
        """
        # Get the URL directly from the page object
        site_url = page.url

        # Initial search on the current page
        logger.info(f"Starting privacy policy search for {site_url}...")
        initial_html = await page.content()
        initial_llm_output = await self._extract_policy_url_from_html(initial_html, site_url)

        found_policies = []
        if initial_llm_output.get("privacy_policy_url"):
            found_policies.append(initial_llm_output)

        # If the initial search didn't find a URL, search internal links
        if not initial_llm_output.get("privacy_policy_url"):
            logger.info("Privacy policy not found on main page. Searching internal links...")
            internal_links = await self._get_internal_links(page, site_url)

            promising_keywords = ['privacy', 'legal', 'terms', 'imprint', 'about', 'contact']
            promising_links = [
                link for link in internal_links
                if any(keyword in link.lower() for keyword in promising_keywords)
            ]

            for link in promising_links:
                try:
                    logger.info(f"Navigating to promising link: {link}")
                    await page.goto(link, wait_until="domcontentloaded", timeout=30000)
                    secondary_html = await page.content()
                    secondary_output = await self._extract_policy_url_from_html(secondary_html, link)
                    if secondary_output.get("privacy_policy_url"):
                        logger.info(f"Found potential privacy policy at: {link}")
                        found_policies.append(secondary_output)
                except Exception as e:
                    logger.warning(f"Could not analyze promising link {link}: {e}")
                    continue

        # If we found any policies, select the one with the highest confidence score
        if len(found_policies) > 0:
            best_policy = max(found_policies, key=lambda x: x.get('confidence_score', 0.0))
            logger.info(f"Selected best privacy policy with score {best_policy.get('confidence_score')}: {best_policy.get('privacy_policy_url')}")
            return best_policy

        # Otherwise, return the original (failed) output
        logger.info("No privacy policy found after deep search.")
        return initial_llm_output

    # --- Cookie Analysis Methods ---
    async def categorize_cookies(self, cookies_data: list):
        """
        Categorizes a list of cookies using the LLM.
        """
        cookies_json_list = json.dumps(cookies_data, indent=2)
        prompt = f"""
        You are an expert in GDPR compliance and a JSON-only generator.
        Your task is to categorize a list of cookies and provide a brief description for each, based on your general knowledge.

        INPUT: A JSON list of raw cookie objects.
        OUTPUT: A single JSON object, with no other text.

        CATEGORIES DEFINITIONS:
        - "Strictly Necessary": Essential for website function (e..g, session, security, shopping cart).
        - "Functional": Remembers user choices (e.g., language, preferences).
        - "Analytical": Collects data on user behavior (e.g., Google Analytics).
        - "Marketing": Tracks users for advertising.
        - "Uncategorized": Unknown or generic purpose.

        INSTRUCTIONS:
        1.  Analyze each cookie in the "Input Cookies" list.
        2.  Based on the cookie's "name" and "domain", categorize it into one of the five categories defined above.
        3.  Create a "description" for each cookie based on your general knowledge (e.g., a cookie named "_ga" is for Google Analytics).
        4.  CRITICAL RULE: If a cookie's name is generic or unknown (e.g., "uid", "session_token"), you MUST set its description to "No specific description available." Do NOT invent a purpose.
        5.  Return a single JSON object with the root key "cookie_categories".
        6.  The value of "cookie_categories" must be a list of objects (one for each category that contains cookies).
        7.  Each category object must contain:
            - "category_name": The name of the category.
            - "cookies": A list of objects for the cookies in that category.
        8.  Each cookie object in the *output* "cookies" list MUST have this structure:
            - "name": The original cookie name.
            - "domain": The original cookie domain.
            - "description": Your generated description (or "No specific description available.").

        DO NOT include any text, explanation, or markdown before or after the JSON object.

        EXAMPLE OF REQUIRED OUTPUT FORMAT:
        {{{{
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
        }}}}

        INPUT COOKIES TO CATEGORIZE:
        {cookies_json_list}
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            logger.error(f"Cookie categorization failed: {response.error}")
            return {{}}
            
        return response.data

    async def _extract_cookie_declaration_url_from_html(self, html_content: str, url: str):
        """
        Analyzes the HTML of a given page to find the cookie declaration page URL.
        """
        prompt = f"""
        You are an expert web analysis agent. Your task is to find the URL of the **human-readable** cookie declaration or cookie settings page.

        CRITICAL RULES:
        1.  MUST BE A NAVIGABLE PAGE: The URL must point to an informational HTML page an end-user can read.
        2.  MUST NOT BE AN ASSET: The URL must NOT be a script (.js), stylesheet (.css), image (.png, .svg), or internal HTML fragment (like 'cookies.built.html' or 'cookie-fragment.html').
        
        SEARCH STRATEGY:
        1.  Analyze the provided HTML for links (`<a>` tags).
        2.  Give priority to links where the *clickable text* (anchor text) contains keywords like 'Cookie Policy', 'Manage Cookies', 'Cookie Settings', 'Cookies and Technologies'.
        3.  If no clear anchor text is found, look at the link's 'href' attribute for keywords like 'cookie', 'technologies', 'privacy'.

        The HTML content to analyze is below:
        ---
        {html_content}
        ---
        
        The base URL of the page is: {url}

        You MUST return a single JSON object and nothing else. Do not include any text or explanation before or after the JSON object.
        Return your answer as a single JSON object with the following structure:
        {{
          "cookie_declaration_url": <string> or null,
          "reasoning": <string>,
          "confidence_score": <number>
        }}

        INSTRUCTIONS FOR JSON FIELDS:
        - "cookie_declaration_url": The full, absolute URL to the page. If you find a relative path (e.g., "/legal/cookies"), you MUST combine it with the base URL ("{url}") to create a full URL.
        - "reasoning": Briefly explain which keywords (in the link text or URL) led you to this choice.
        - "confidence_score": From 0.0 to 1.0, how certain you are.
        - If no valid URL is found, set "cookie_declaration_url" to null.
        """
        
        response = await self.llm_client.query_json(user_prompt=prompt)
        
        if not response.success:
            return {
                "cookie_declaration_url": None,
                "reasoning": response.error,
                "confidence_score": 0.0
            }
        
        # Ensure the URL is absolute
        llm_returned_url = response.data.get("cookie_declaration_url")
        if llm_returned_url:
            absolute_url = urljoin(url, llm_returned_url)
            response.data["cookie_declaration_url"] = absolute_url
            
            # Post-process to ensure the URL is a valid HTML page
            if not self._is_valid_html_url(absolute_url):
                logger.warning(f"LLM returned an invalid URL for cookie declaration: {absolute_url}. Setting to None.")
                response.data["cookie_declaration_url"] = None
                response.data["reasoning"] += " (URL was filtered as it was not a valid HTML page.)"
                response.data["confidence_score"] = 0.0

        return response.data

    async def find_cookie_declaration_page(self, browser, site_url: str) -> Dict[str, Any]:
        """
        Orchestrates a deep search for the cookie declaration URL, mirroring the privacy policy search strategy.
        """
        page = None
        try:
            # Initial search on the current page
            logger.info(f"Starting cookie declaration search for {site_url}...")
            page = await browser.new_page()
            await page.goto(site_url, timeout=60000)
            initial_html = await page.content()
            initial_llm_output = await self._extract_cookie_declaration_url_from_html(initial_html, site_url)

            found_declarations = []
            if initial_llm_output.get("cookie_declaration_url"):
                found_declarations.append(initial_llm_output)

            # If the initial search didn't find a URL, search internal links
            if not initial_llm_output.get("cookie_declaration_url"):
                logger.info("Cookie declaration not found on main page. Searching internal links...")
                internal_links = await self._get_internal_links(page, site_url)

                promising_keywords = ['cookie', 'technologies', 'legal', 'privacy', 'imprint']
                promising_links = [
                    link for link in internal_links
                    if any(keyword in link.lower() for keyword in promising_keywords)
                ]

                for link in promising_links:
                    try:
                        logger.info(f"Navigating to promising link for cookie declaration: {link}")
                        await page.goto(link, wait_until="domcontentloaded", timeout=30000)
                        secondary_html = await page.content()
                        secondary_output = await self._extract_cookie_declaration_url_from_html(secondary_html, link)
                        if secondary_output.get("cookie_declaration_url"):
                            logger.info(f"Found potential cookie declaration at: {link}")
                            found_declarations.append(secondary_output)
                    except Exception as e:
                        logger.warning(f"Could not analyze promising link {link}: {e}")
                        continue

            # If we found any declarations, select the one with the highest confidence score
            if len(found_declarations) > 0:
                best_declaration = max(found_declarations, key=lambda x: x.get('confidence_score', 0.0))
                logger.info(
                    f"Selected best cookie declaration with score {best_declaration.get('confidence_score')}: {best_declaration.get('cookie_declaration_url')}")
                await page.close()
                return best_declaration

            # Otherwise, return the original (failed) output
            logger.info("No cookie declaration found after deep search.")
            await page.close()
            return initial_llm_output
        
        except Exception as e:
            logger.error(f"Error during cookie declaration search for {site_url}: {e}")
            if page:
                await page.close()
            return {"reasoning": f"Failed during cookie declaration search: {e}", "cookie_declaration_url": None}

    # --- Data Retention Methods ---
    async def _analyze_retention_from_html(self, privacy_policy_html: str, url: str):
        """
        Analyzes the HTML of a privacy policy page to find data retention information.
        """
        prompt = f"""
        You are an expert in GDPR compliance and a meticulous text extractor.
        Your task is to find and summarize the data retention policy from the provided HTML.

        CRITICAL RULES:
        1.  NO HALLUCINATIONS: You MUST NOT invent information. Your summary must be 100% based *only* on the text found. If the text is vague (e.g., "as long as necessary"), your summary MUST be vague. Do not add specific details (like "12 months") if they are not explicitly written.
        2.  STRICT EXTRACTION: Your primary goal is to find the *exact* text.
        3.  SECTION IDENTIFICATION: You must identify the heading or title of the section where you found the information (e.g., "Retention of Personal Data").

        The URL of the page being analyzed is: {url}

        Privacy Policy HTML content:
        ---
        {privacy_policy_html}
        ---
        
        You MUST return a single JSON object and nothing else. Do not include any text or explanation before or after the JSON object.
        Return your answer as a single JSON object with the following structure:
        {{
          "retention_policy_summary": <string> or null,
          "source_section": <string> or null,
          "reasoning": <string>,
          "confidence_score": <number>,
          "retention_policy_url": "{url}"
        }}

        INSTRUCTIONS FOR JSON FIELDS:
        - "retention_policy_summary": A brief summary of the policy.".
        - "source_section": The *exact* text of the nearest section heading (e.g., "Data Retention", "How We Keep Your Data").
        - "reasoning": Briefly explain *why* you chose this section and text.
        - "confidence_score": From 0.0 to 1.0, how certain you are.
        
        If no retention policy is found, set "retention_policy_summary" and "source_section" to null.
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

    async def analyze_retention_policy(self, browser, policy_url: str) -> Dict[str, Any]:
        """
        Navigates to a privacy policy page and analyzes it for retention info.
        """
        page = None
        try:
            page = await browser.new_page()
            await page.goto(policy_url, timeout=60000)
            privacy_policy_html = await page.content()

            logger.info(f"Analyzing for data retention: {policy_url}")
            retention_output = await self._analyze_retention_from_html(privacy_policy_html, policy_url)
            
            await page.close()
            return retention_output

        except Exception as e:
            logger.error(f"Error analyzing privacy page {policy_url}: {e}")
            if page:
                await page.close()
            return {"reasoning": f"Failed during privacy page analysis: {e}", "source_url": policy_url}

    # --- Data Deletion Methods ---
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
        
        llm_returned_url = response.data.get("deletion_page_url")
        if llm_returned_url and not self._is_valid_html_url(llm_returned_url):
            logger.warning(f"LLM returned an invalid URL for data deletion: {llm_returned_url}. Setting to None.")
            response.data["deletion_page_url"] = None
            response.data["reasoning"] += " (URL was filtered as it was not a valid HTML page.)"
            response.data["confidence_score"] = 0.0

        return response.data

    async def _analyze_deletion_sub_page_for_fan_out(self, page, url: str, site_url: str, hop_num: int) -> Dict[str, Any]:
        """
        Helper to analyze a sub-page for the data deletion URL during fan-out search.
        """
        try:
            logger.info(f"Analyzing for data deletion (Fan-out Hop {hop_num}): {url}")
            await page.goto(url, timeout=60000)
            html = await page.content()
            deletion_output = await self.extract_data_deletion_url_from_page(html, url)
            return deletion_output
        except Exception as e:
            logger.error(f"Error analyzing data deletion sub-page {url}: {e}")
            return {}
        finally:
            await page.close()

    async def find_data_deletion_page(self, browser, site_url: str) -> Dict[str, Any]:
        """
        Navigates the site to find the data deletion/management page (e.g., Privacy Dashboard).
        'site_url' should be the URL of the main privacy policy page.
        """
        page = None
        try:
            logger.info(f"Starting data deletion page search for: {site_url}")
            
            page = await browser.new_page()
            await page.goto(site_url, timeout=60000)
            html_content = await page.content()
            initial_output = await self.extract_data_deletion_url_from_page(html_content, site_url)
            
            final_output = {}

            if initial_output.get('deletion_page_url') and self._is_valid_html_url(initial_output.get('deletion_page_url')):
                logger.info(f"Data deletion page found on initial page: {initial_output.get('deletion_page_url')}")
                final_output = initial_output
            else:
                logger.info("Data deletion page not found. Starting fan-out search.")
                
                internal_links = await self._get_internal_links(page, site_url)
                
                promising_keywords = [
                    'privacy', 'account', 'delete', 'erasure', 'rights', 
                    'manage', 'control', 'dashboard', 'data'
                ]
                promising_links = [
                    link for link in internal_links 
                    if any(keyword in link.lower() for keyword in promising_keywords) and self._is_valid_html_url(link)
                ]
                
                search_tasks = []
                for i, link in enumerate(promising_links):
                    if i >= self.max_hops:
                        logger.warning(f"Reached max_hops limit ({self.max_hops}).")
                        break
                    
                    task_page = await browser.new_page()
                    task = asyncio.create_task(self._analyze_deletion_sub_page_for_fan_out(task_page, link, site_url, i + 1))
                    search_tasks.append(task)
                
                found_pages = await asyncio.gather(*search_tasks)
                
                valid_pages = [p for p in found_pages if p and p.get('deletion_page_url') and self._is_valid_html_url(p.get('deletion_page_url'))]
                
                if valid_pages:
                    final_output = valid_pages[0] # Take the first valid result
                    logger.info(f"Data deletion page found via fan-out search: {final_output.get('deletion_page_url')}")
                else:
                    final_output = {"reasoning": "No data deletion page found after search.", "source_url": site_url}

            await page.close()
            return final_output

        except Exception as e:
            logger.error(f"Error during data deletion page search for {site_url}: {e}")
            if page:
                await page.close()
            return {"reasoning": f"Failed during data deletion search: {e}", "source_url": None}

    # --- DPO Methods ---
    async def extract_dpo_info_from_page(self, html_content: str, url: str):
        """
        Analyzes the HTML of a given page to find DPO contact information.
        """
        dpo_prompt = f"""
        You are an expert in GDPR compliance and a pure text extractor.

        **STRICT RULE:** You MUST only use the text provided in the HTML content below. Do not use any external knowledge or web search. Your task is to extract information ONLY from the provided text.

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

    async def _analyze_dpo_sub_page_for_fan_out(self, page, url: str, site_url: str, hop_num: int) -> Dict[str, Any]:
        """
        Helper to analyze a sub-page for DPO info during fan-out search.
        """
        try:
            logger.info(f"Analyzing for DPO (Fan-out Hop {hop_num}): {url}")
            await page.goto(url, timeout=60000)
            html = await page.content()
            dpo_output = await self.extract_dpo_info_from_page(html, url)
            return dpo_output
        except Exception as e:
            logger.error(f"Error analyzing DPO sub-page {url}: {e}")
            return {}
        finally:
            await page.close()

    async def find_dpo(self, browser, site_url: str) -> Dict[str, Any]:
        """
        Navigates the site to find the DPO information page and extracts DPO contact details.
        """
        page = None
        try:
            logger.info(f"Starting DPO search for: {site_url} using internal link navigation.")
            
            # 1. Initial page load and DPO info extraction
            page = await browser.new_page()
            await page.goto(site_url, timeout=60000)
            html_content = await page.content()
            initial_dpo_output = await self.extract_dpo_info_from_page(html_content, site_url)
            
            final_dpo_output = {}

            if initial_dpo_output.get('email_address'):
                logger.info(f"DPO found on initial page: {initial_dpo_output.get('email_address')}")
                final_dpo_output = initial_dpo_output
            else:
                logger.info("DPO email not found directly. Starting fan-out search for DPO.")
                
                internal_links = await self._get_internal_links(page, site_url)
                
                promising_keywords = ['dpo', 'data protection', 'governance', 'legal', 'contact', 'privacy']
                promising_links = [
                    link for link in internal_links 
                    if any(keyword in link.lower() for keyword in promising_keywords)
                ]
                
                dpo_search_tasks = []
                for i, link in enumerate(promising_links):
                    if i >= self.max_hops:
                        logger.warning(f"Reached max_hops limit ({self.max_hops}). Not all promising links will be checked.")
                        break
                    
                    task_page = await browser.new_page()
                    task = asyncio.create_task(self._analyze_dpo_sub_page_for_fan_out(task_page, link, site_url, i + 1))
                    dpo_search_tasks.append(task)
                
                found_dpos = await asyncio.gather(*dpo_search_tasks)
                
                valid_dpos = [dpo for dpo in found_dpos if dpo and dpo.get('email_address')]
                
                if valid_dpos:
                    final_dpo_output = valid_dpos[0]
                    logger.info(f"DPO found via fan-out search: {final_dpo_output.get('email_address')}")
                else:
                    final_dpo_output = {"reasoning": "No DPO information found after extensive internal link search.", "source_url": site_url}

            await page.close()
            return final_dpo_output

        except Exception as e:
            logger.error(f"Error during DPO search for {site_url}: {e}")
            if page:
                await page.close()
            return {"reasoning": f"Failed during DPO search: {e}", "source_url": None}

    # --- Generic Utility Methods ---
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

    def _is_valid_html_url(self, url: str) -> bool:
        """
        Checks if a URL is likely an HTML page and not a script or asset file.
        """
        if not url:
            return False
        # Common file extensions that are not HTML pages
        excluded_extensions = ('.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.pdf', '.xml', '.json', '.zip', '.rar', '.tar', '.gz', '.svg', '.ico')
        if url.lower().endswith(excluded_extensions):
            return False
        return True
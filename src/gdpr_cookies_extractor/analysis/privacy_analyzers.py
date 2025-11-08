import json
import logging
from urllib.parse import urljoin, urlparse
from typing import Dict, Any, List, Optional
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

    async def _extract_policy_url_from_html(self, html_content: str, url: str):
    # --- Privacy Policy Methods ---
        """
        Sends HTML content to the LLM to find the privacy policy URL on a single page.
        """
        prompt = f"""
        You are an expert web analysis agent. Your task is to find the URL of the privacy policy page for the given website.
        This page is often linked from the footer with a 'Privacy' word or similar. Notice that the cookie policy and the privacy policy could be on different url so be sure to return the privacy polcy and note the cookie policy. 
        Analyze the provided HTML content and find the most likely URL for the privacy policy.
        Look for links containing keywords like 'privacy policy', 'GDPR', 'data protection', 'privacy center'.
        
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
        privacy_policy_url must be the complete URL to the privacy page. If no URL is found, set "privacy_policy_url" to null.
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

    async def _analyze_page_for_policy(self, page, url: str, hop_num: int, original_root_domain: str, user_keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        [WORKER FUNCTION]
        Analyzes a SINGLE page (URL) for a policy link and calculates a keyword bonus.
        This is the atomic work unit for policy search.
        It is responsible for closing its own page.
        """
        html_lower = ""
        try:
            logger.info(f"Analyzing page (Hop {hop_num}): {url}")
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")

            # Check for external redirect after navigation
            final_netloc = urlparse(page.url).netloc
            if not (final_netloc == original_root_domain or final_netloc.endswith("." + original_root_domain)):
                logger.warning(f"Redirected to external domain: {page.url}. Skipping analysis.")
                return {
                    "privacy_policy_url": None,
                    "reasoning": f"Redirected to external domain {page.url}",
                    "confidence_score": 0.0,
                    "keyword_bonus": 0.0
                }

            html = await page.content()
            html_lower = html.lower() # Store for bonus calculation

            # call LLM to extract policy URL from the HTML
            policy_output = await self._extract_policy_url_from_html(html, url)

            # calculate keyword bonus
            # this bonus is based on finding keywords on the current page,
            keyword_bonus = 0.0
            if user_keywords:
                if any(keyword.lower() in html_lower for keyword in user_keywords):
                    logger.info(f"User keywords found on {url}, applying bonus.")
                    keyword_bonus = 0.3 # Fixed bonus
            
            policy_output['keyword_bonus'] = keyword_bonus

            # ensure the found URL is absolute
            found_url = policy_output.get("privacy_policy_url")
            if found_url:
                policy_output["privacy_policy_url"] = urljoin(url, found_url)

            return policy_output
        
        except Exception as e:
            logger.error(f"Error analyzing page {url}: {e}")
            return {
                "privacy_policy_url": None,
                "reasoning": f"Failed to analyze page {url}: {e}",
                "confidence_score": 0.0,
                "keyword_bonus": 0.0
            }
        finally:
            if page:
                await page.close()

    async def find_privacy_policy(self, browser, site_url: str, filter_keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        [ORCHESTRATOR FUNCTION]
        Orchestrates the search for the privacy policy URL.
        It uses _analyze_page_for_policy for both the initial page
        and the parallel fan-out search.
        """
        found_policies = []
        initial_result = None
        
        try:
            logger.info(f"Starting privacy policy search for {site_url}...")
            
            # Determine the root domain to check against redirects
            base_netloc = urlparse(site_url).netloc
            root_domain = base_netloc[4:] if base_netloc.startswith("www.") else base_netloc
            
            # INITIAL ANALYSIS ---
            # Use the worker function for the main site_url
            initial_page = await browser.new_page()
            initial_result = await self._analyze_page_for_policy(
                initial_page, site_url, 0, root_domain, filter_keywords
            )
            
            if initial_result and initial_result.get("privacy_policy_url"):
                found_policies.append(initial_result)

            
            # FAN-OUT SEARCH ---
            # Only run fan-out if the initial page analysis failed
            # if not initial_result.get("privacy_policy_url"):
            if True:
                logger.info("Policy not found on main page. Starting fan-out search...")
                
                
                temp_page = await browser.new_page()
                promising_links = []
                try:
                    await temp_page.goto(site_url, timeout=60000)
                    promising_links = await self._filter_internal_links(temp_page, site_url, filter_keywords)
                    logger.debug(f"Interanl links found: {promising_links}")
                except Exception as e:
                    logger.error(f"Failed to get internal links for fan-out: {e}")
                finally:
                    await temp_page.close()

                # Create parallel search tasks
                search_tasks = []
                for i, link in enumerate(promising_links):
                    if i >= self.max_hops:
                        logger.warning(f"Reached max_hops limit ({self.max_hops}).")
                        break
                    
                    task_page = await browser.new_page()
                    task = asyncio.create_task(self._analyze_page_for_policy(
                        task_page, link, i + 1, root_domain, filter_keywords
                    ))
                    search_tasks.append(task)
                
                # Collect results from fan-out
                if search_tasks:
                    found_from_fan_out = await asyncio.gather(*search_tasks)
                    # Add valid results to our list
                    found_policies.extend([
                        p for p in found_from_fan_out 
                        if p and p.get("privacy_policy_url")
                    ])

            # FINAL SELECTION ---
            if found_policies:
                # Use a hybrid score to find the best policy
                def calculate_hybrid_score(policy):
                    confidence = policy.get('confidence_score', 0.0)
                    bonus = policy.get('keyword_bonus', 0.0)
                    # Score is 70% confidence, 30% bonus
                    return (0.7 * confidence) + (0.3 * bonus)

                best_policy = max(found_policies, key=calculate_hybrid_score)
                hybrid_score = calculate_hybrid_score(best_policy)
                
                logger.info(f"Selected best privacy policy with hybrid score {hybrid_score:.2f}: {best_policy.get('privacy_policy_url')}")
                return best_policy

            # If no policies were found at all, return the (empty) initial result
            logger.info("No privacy policy found after deep search.")
            return initial_result
        
        except Exception as e:
            logger.error(f"Critical error during privacy policy search for {site_url}: {e}")
            return {"reasoning": f"Failed during privacy policy search: {e}", "privacy_policy_url": None}

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
            return {}
            
        return response.data


    # --- Generic Utility Methods ---
    async def _filter_internal_links(self, page, site_url: str, filter_keywords: Optional[List[str]] = None)  -> List[str]:
        """
        Helper to extract all internal links (including subdomains) from a page.
        """
        links = []
        if filter_keywords is None:
            filter_keywords = []
        lower_keywords = [k.lower() for k in filter_keywords]

        base_netloc = urlparse(site_url).netloc
        # remove "www."  to get the root
        root_domain = base_netloc[4:] if base_netloc.startswith("www.") else base_netloc 
        
        for a in await page.query_selector_all('a'):
            href = None
            try:
                href = await a.get_attribute('href')
                if href:
                    full_url = urljoin(site_url, href)
                    link_netloc = urlparse(full_url).netloc 
                    
                    # conditions
                    is_exact_domain = (link_netloc == root_domain)
                    is_subdomain = link_netloc.endswith("." + root_domain)
                    
                    if (is_exact_domain or is_subdomain) and \
                       '#' not in full_url and \
                       not full_url.endswith(('.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.pdf', '.xml', '.json', '.zip', '.rar', '.tar', '.gz', '.svg', '.ico')):
                        

                        #filtering for specific keywords
                        text_content = await a.inner_text()
                        search_area = href.lower() + " " + text_content.strip().lower()
                        if not lower_keywords or any(keyword in search_area for keyword in lower_keywords):
                            links.append(full_url)

                        
            except Exception as e:
                logger.debug(f"Could not process link {href}: {e}")

        return list(set(links)) # Return unique links
    
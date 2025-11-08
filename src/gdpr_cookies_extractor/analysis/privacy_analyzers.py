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

    async def _extract_policy_url_from_html(self, html_content: str, url: str, promising_links: List[str]):
    # --- Privacy Policy Methods ---
        """
        Sends HTML content to the LLM to find the privacy policy URL on a single page.
        """
        prompt = f"""
        You are an expert web analysis agent. Your task is to find the URL of the privacy policy page of this site {url}.
        
        A pre-filtered list of candidate links has been provided: {promising_links}, so choose from these links the most valuable candidate for privacy page. 

        **CRITICAL RULE: If the candidate link list is not empty, you choose the best and most relevant option from that list. Only if the candidates list is empty you can search in the HTML content.** 
        
        When searching, look for links containing keywords like 'privacy policy', 'GDPR', 'data protection', 'privacy center'.
        The privacy policy is often in the footer of the page. Note that the cookie policy and the privacy policy could be on different URLs, so be sure to return the main privacy policy.
        Notice that cookie page and privage page could be on separate pages so do not return return the cookie page in palce of privacy page. 

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
        - privacy_policy_url: Must be the complete and absolute URL to the privacy page. If no URL is found, this MUST be null.
        - reasoning: Explain your choice or why you could not find a URL.
        - confidence_score: A number from 0.0 to 1.0 indicating your certainty.
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
        Analyzes a SINGLE page (URL) for a policy link, validates the LLM's choice, and calculates a keyword bonus.
        This is the atomic work unit for policy search.
        """
        html_lower = ""
        try:
            logger.info(f"Analyzing page (Hop {hop_num}): {url}")
            if not page.url == url:
                await page.goto(url, timeout=60000, wait_until="domcontentloaded")

            # Step 1: Get heuristic candidates as rich objects ({href, text})
            promising_links_objects = await self._filter_internal_links(page, url, user_keywords)
            logger.debug(f"Internal links found: {promising_links_objects}")

            # Check for external redirect after navigation
            final_netloc = urlparse(page.url).netloc
            if not (final_netloc == original_root_domain or final_netloc.endswith("." + original_root_domain)):
                logger.warning(f"Redirected to external domain: {page.url}. Skipping analysis.")
                return {"privacy_policy_url": None, "reasoning": f"Redirected to external domain {page.url}", "confidence_score": 0.0, "keyword_bonus": 0.0}

            html = await page.content()
            html_lower = html.lower()

            # Step 2: Call LLM with a simple list of hrefs for the prompt
            href_list_for_llm = [link['href'] for link in promising_links_objects]
            policy_output = await self._extract_policy_url_from_html(html, url, href_list_for_llm)
            llm_url = policy_output.get("privacy_policy_url")
            logger.debug(f"Returned choice from LLM: {llm_url}")

            # Step 3: Validate the LLM's choice and apply heuristic override if needed
            if promising_links_objects and llm_url:
                # Check if the LLM's choice is valid (i.e., it's one of the promising hrefs)
                is_llm_choice_valid = any(llm_url in link_obj['href'] for link_obj in promising_links_objects)
                
                if not is_llm_choice_valid:
                    logger.warning(f"LLM disobeyed prompt. Its choice '{llm_url}' was not in the candidate list. Applying heuristic fallback.")
                    
                    # Use the programmatic selector with the rich link objects
                    heuristic_url = self._get_best_candidate(promising_links_objects, user_keywords)
                    
                    if heuristic_url:
                        logger.info(f"Heuristic override selected: '{heuristic_url}'")
                        policy_output["privacy_policy_url"] = heuristic_url
                        policy_output["reasoning"] = "LLM choice overridden by heuristic due to non-compliance. Selected best candidate from pre-filtered list."
                        policy_output["confidence_score"] = 0.95 # High confidence in our heuristic
                    else:
                        logger.warning("Heuristic fallback found no suitable link either.")
            
            # Step 4: Calculate keyword bonus
            keyword_bonus = 0.0
            if user_keywords and any(keyword.lower() in html_lower for keyword in user_keywords):
                logger.info(f"User keywords found on {url}, applying bonus.")
                keyword_bonus = 0.3
            
            policy_output['keyword_bonus'] = keyword_bonus

            # Step 5: Ensure the final URL is absolute
            found_url = policy_output.get("privacy_policy_url")
            if found_url:
                policy_output["privacy_policy_url"] = urljoin(url, found_url)

            return policy_output
        
        except Exception as e:
            logger.error(f"Error analyzing page {url}: {e}")
            return {"privacy_policy_url": None, "reasoning": f"Failed to analyze page {url}: {e}", "confidence_score": 0.0, "keyword_bonus": 0.0}
        finally:
            if page:
                await page.close()

    async def find_privacy_policy(self, context, site_url: str, filter_keywords: Optional[List[str]] = None) -> Dict[str, Any]:
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
            initial_page = await context.new_page()
            
            initial_result = await self._analyze_page_for_policy(
                initial_page, site_url, 0, root_domain, filter_keywords
            )
            
            if initial_result and initial_result.get("privacy_policy_url"):
                found_policies.append(initial_result)


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



    ############################################################################### UTILITY FUNCTIONS ###############################################################################

    # --- Generic Utility Methods ---
    async def _filter_internal_links(self, page, site_url: str, filter_keywords: Optional[List[str]] = None) -> List[Dict[str, str]]:
        """
        Helper to extract all internal links (including subdomains) from a page,
        returning both the URL and the anchor text.
        """
        logger.debug(f"filtering keywords: {filter_keywords}")
        links = []
        unique_hrefs = set()

        if filter_keywords is None:
            filter_keywords = []
        lower_keywords = [k.lower() for k in filter_keywords]

        base_netloc = urlparse(site_url).netloc
        root_domain = base_netloc[4:] if base_netloc.startswith("www.") else base_netloc 
        
        for a in await page.query_selector_all('a'):
            href = None
            try:
                href = await a.get_attribute('href')
                if href:
                    full_url = urljoin(site_url, href)
                    
                    # Avoid duplicates
                    if full_url in unique_hrefs:
                        continue

                    link_netloc = urlparse(full_url).netloc 
                    
                    is_exact_domain = (link_netloc == root_domain)
                    is_subdomain = link_netloc.endswith("." + root_domain)
                    
                    if (is_exact_domain or is_subdomain) and '#' not in full_url and not full_url.endswith(('.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.pdf', '.xml', '.json', '.zip', '.rar', '.tar', '.gz', '.svg', '.ico')):
                        text_content = (await a.inner_text() or "").strip()
                        search_area = href.lower() + " " + text_content.lower()
                        
                        if not lower_keywords or any(keyword in search_area for keyword in lower_keywords):
                            logger.debug(f"Adding link {full_url} with text='{text_content}'")
                            links.append({"href": full_url, "text": text_content})
                            unique_hrefs.add(full_url)
            except Exception as e:
                logger.debug(f"Could not process link {href}: {e}")

        return links
    
    def _get_best_candidate(self, promising_links: List[Dict[str, str]], keyword_priority_list: List[str]) -> Optional[str]:
        """
        Selects the best URL from a list of candidates using a weighted scoring system
        based on a prioritized list of keywords.
        """
        if not promising_links or not keyword_priority_list:
            return None

        best_link_href = None
        max_score = -1
        num_keywords = len(keyword_priority_list)

        for link_data in promising_links:
            current_score = 0
            # Iterate through keywords to calculate a score for the current link
            for i, keyword in enumerate(keyword_priority_list):
                # Higher priority keywords (earlier in the list) get a higher base weight
                weight = num_keywords - i
                
                # Split keyword phrase into individual words
                required_words = keyword.lower().split()

                # Give a higher score for matches in the anchor text (strong signal)
                if all(word in link_data["text"].lower() for word in required_words):
                    current_score += weight * 2
                
                # Give a lower score for matches in the URL itself
                if all(word in link_data["href"].lower() for word in required_words):
                    current_score += weight

            # Update the best link if the current one has a better score
            if current_score > max_score:
                max_score = current_score
                best_link_href = link_data["href"]
            # Tie-breaker: if scores are equal, prefer the shorter link
            elif current_score == max_score and best_link_href:
                if len(link_data["href"]) < len(best_link_href):
                    best_link_href = link_data["href"]
        
        if best_link_href:
            logger.info(f"Heuristic selection: chose '{best_link_href}' with score {max_score}")
        else:
            logger.info("Heuristic selection: no suitable candidate found.")

        return best_link_href
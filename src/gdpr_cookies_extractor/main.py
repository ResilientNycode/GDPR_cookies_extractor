import pandas as pd
import json
import asyncio
import sys
from playwright.async_api import async_playwright
from datetime import datetime
from urllib.parse import urlparse, urljoin
import logging

# Import functions from the new modules
from .utils.logging_setup import *
from .analysis.llm_api import *
from .analysis.scraper import *

# Get a logger instance for the main module
logger = logging.getLogger(__name__)

async def main_async(sites_df):
    """
    The main asynchronous function that orchestrates the entire process.
    """
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        for index, row in sites_df.iterrows():
            site_url = row['website_url']

            if not site_url.startswith('http://') and not site_url.startswith('https://'):
                site_url = 'https://' + site_url

            scenarios = ["accept", "reject"]
            for scenario in scenarios:
                logger.info(f"Processing: {site_url} with scenario: {scenario}")
                
                try:
                    page = await browser.new_page()
                    
                    logger.info("Fetching cookies and HTML...")
                    await page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
                    
                    await handle_cookie_banner(page, action=scenario)

                    await page.wait_for_timeout(3000)
                    cookies = await page.context.cookies()
                    logger.info(f"Captured {len(cookies)} cookies after '{scenario}' action.")
                    
                    # Call the new function to categorize the cookies
                    logger.info("Sending cookies to LLM for categorization...")
                    cookie_categories = await categorize_cookies(cookies)
                    logger.info("Cookie categorization complete.")
                    logger.info(f"Categorized Cookies: {cookie_categories}")
                    
                    base_domain = urlparse(site_url).netloc
                    if base_domain.startswith('www.'):
                        base_domain = base_domain[4:]

                    third_party_count = 0
                    for cookie in cookies:
                        cookie_domain = cookie['domain']
                        if cookie_domain.startswith('.'):
                            cookie_domain = cookie_domain[1:]
                        
                        if cookie_domain != base_domain:
                            third_party_count += 1
                
                    logger.info(f"Detected {third_party_count} third-party cookies.")
                    
                    html_content = await page.content()
                    
                    simple_extractor(html_content)
                    
                    logger.info("Sending HTML to LLM for analysis...")
                    llm_output = await find_privacy_policy(html_content, site_url)
                    logger.info("LLM task complete. Processing response.")
                    logger.info(llm_output)

                    dpo_output = {"dpo_found": False, "contact_info": None, "reasoning": "No privacy policy URL found."}
                    retention_output = {"retention_found": False, "retention_policy_summary": None, "reasoning": "No privacy policy URL found."}

                    if llm_output.get("result_found"):
                        privacy_policy_url = llm_output.get("privacy_policy_url")
                        
                        full_privacy_policy_url = urljoin(site_url, privacy_policy_url)
                        logger.info(f"  Navigating to privacy policy page: {full_privacy_policy_url}")
                        
                        try:
                            # 1. INITIAL HOP: Navigate to the general privacy policy page
                            dpo_page = await browser.new_page()
                            await dpo_page.goto(full_privacy_policy_url, timeout=60000)
                            privacy_policy_html = await dpo_page.content()

                            # Run Retention Analysis on the initial page (since it's a summary page)
                            logger.info("Analyzing privacy policy page for data retention...")
                            retention_output = await analyze_retention_policy(privacy_policy_html)
                            logger.info("Data retention analysis complete.")
                            
                            # Run DPO analysis on the initial page. This will return the DPO OR a sub_link.
                            logger.info("Analyzing privacy policy page for DPO information (Initial Hop)...")
                            dpo_output = await find_dpo(privacy_policy_html, full_privacy_policy_url)
                            logger.info(f"Initial DPO analysis complete. Sub-link found: {dpo_output.get('sub_link')}")
                            
                            
                            # 2. MULTI-HOP CHECK: If DPO wasn't found and a sub_link was returned...
                            if not dpo_output.get('dpo_found') and dpo_output.get('sub_link'):
                                sub_link_url = dpo_output.get('sub_link')
                                full_sub_link_url = urljoin(full_privacy_policy_url, sub_link_url)
                                
                                # --- START OF FIX: Prevent self-referencing links ---
                                # Normalize URLs for strict comparison
                                normalized_current_url = full_privacy_policy_url.rstrip('/')
                                normalized_next_url = full_sub_link_url.rstrip('/')
                                
                                if normalized_next_url == normalized_current_url:
                                    logger.info("  ⚠️ Multi-Hop skipped: Sub-link is redundant (same as current page).")
                                    # We stop here because the LLM didn't find a new page to go to.
                                    # The final dpo_output is the result from the initial analysis.
                                    pass 
                                else:
                                    # --- Execute the valid second hop ---
                                    logger.info(f"  Performing Multi-Hop: Navigating to sub-link: {full_sub_link_url}")
                                    
                                    # Navigate to the sub-link and get its HTML
                                    # Use the same page object to avoid creating a new browser page
                                    await dpo_page.goto(full_sub_link_url, timeout=60000)
                                    sub_link_html = await dpo_page.content()
                                    
                                    # Re-run DPO analysis on the new, specific page (The Second Hop)
                                    logger.info("  🧠 Re-analyzing new page for DPO (Second Hop)...")
                                    second_dpo_output = await find_dpo(sub_link_html, full_sub_link_url)
                                    logger.info("  ✅ Multi-Hop DPO analysis complete.")
                                    
                                    # Overwrite the result with the second, more specific hop's result
                                    dpo_output = second_dpo_output
                            
                            await dpo_page.close()
                            
                        except Exception as e:
                            logger.error(f"Error during privacy page navigation or multi-hop process: {e}")
                            dpo_output = {"dpo_found": False, "contact_info": None, "reasoning": f"Failed during navigation/multi-hop: {e}"}
                            
                    
                    await page.close()
                    
                    results.append({
                        "website_url": site_url,
                        "scenario": scenario, # Add the scenario to the result
                        "privacy_policy_url": llm_output.get("privacy_policy_url"),
                        "llm_found": llm_output.get("result_found"),
                        "llm_reasoning": llm_output.get("reasoning"),
                        "dpo_contact_info": dpo_output.get("contact_info"),
                        "dpo_found": dpo_output.get("dpo_found"),
                        "dpo_reasoning": dpo_output.get("reasoning"),
                        "retention_policy_summary": retention_output.get("retention_policy_summary"),
                        "retention_found": retention_output.get("retention_found"),
                        "retention_reasoning": retention_output.get("reasoning"),
                        "cookies_count": len(cookies),
                        "third_party_cookies_count": third_party_count,
                        "raw_cookies_data": json.dumps(cookies),
                        "categorized_cookies": json.dumps(cookie_categories)
                    })

                except Exception as e:
                    logger.error(f"Error processing {site_url} in '{scenario}' scenario: {e}")
                    results.append({
                        "website_url": site_url,
                        "scenario": scenario,
                        "privacy_policy_url": "N/A",
                        "llm_found": False,
                        "llm_reasoning": f"Failed to process: {e}",
                        "dpo_contact_info": "N/A",
                        "dpo_found": False,
                        "dpo_reasoning": f"Failed to process: {e}",
                        "retention_policy_summary": "N/A",
                        "retention_found": False,
                        "retention_reasoning": f"Failed to process: {e}",
                        "cookies_count": 0,
                        "third_party_cookies_count": 0,
                        "raw_cookies_data": "[]",
                        "categorized_cookies": "[]"
                    })
        
        await browser.close()

    results_df = pd.DataFrame(results)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"analysis_results_{timestamp}.json"
    results_df.to_json(filename, index=False)
    logger.info("Analysis complete. Results saved to %s", filename)

def main():
    """
    The synchronous entry point for the script.
    It now handles either a command-line URL or a CSV file.
    """
    setup_logging()
    
    logger.info("Running via Poetry script...")

    if len(sys.argv) > 1:
        site_url_from_cli = sys.argv[1]
        sites_df = pd.DataFrame([{'website_url': site_url_from_cli}])
    else:
        try:
            sites_df = pd.read_csv("sites.csv", header=None, names=['index_col', 'website_url'])
            sites_df = sites_df.drop(columns=['index_col'])
            if 'website_url' not in sites_df.columns:
                logger.error("The CSV file must contain a 'website_url' column.")
                sys.exit(1)
        except FileNotFoundError:
            logger.error("No URL provided and 'sites.csv' file not found.")
            logger.error("Usage: poetry run main <your_url> or place a 'sites.csv' file in the directory.")
            sys.exit(1)

    asyncio.run(main_async(sites_df))

if __name__ == "__main__":
    main()
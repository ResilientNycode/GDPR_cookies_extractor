import pandas as pd
import json
import asyncio
import sys
import logging
from playwright.async_api import async_playwright
from datetime import datetime
from urllib.parse import urlparse, urljoin
from typing import List, Dict, Any

from .utils.logging_setup import *
from .analysis.scraper import handle_cookie_banner, simple_extractor
from .analysis.ollama_providers import OllamaProvider
from .analysis.privacy_analyzers import PrivacyAnalyzer
from .analysis.llm_interface import AbstractLLMClient

logger = logging.getLogger(__name__)


async def analyze_privacy_policy_page(browser, analyzer: PrivacyAnalyzer, policy_url: str) -> tuple:
    """
    Navigates to a privacy policy page and analyzes it for DPO and retention info.
    Handles the multi-hop logic for DPO discovery.
    """
    dpo_output = {}
    retention_output = {}

    try:
        page = await browser.new_page()
        await page.goto(policy_url, timeout=60000)
        privacy_policy_html = await page.content()

        # Run Retention Analysis
        logger.info(f"Analyzing for data retention: {policy_url}")
        retention_output = await analyzer.analyze_retention_policy(privacy_policy_html)
        
        # Run DPO analysis (Initial Hop)
        logger.info(f"Analyzing for DPO (Hop 1): {policy_url}")
        dpo_output = await analyzer.find_dpo(privacy_policy_html, policy_url)
        
        # Multi-Hop Check: If DPO not found and a valid sub_link exists
        if not dpo_output.get('dpo_found') and dpo_output.get('sub_link'):
            sub_link_url = dpo_output.get('sub_link')
            full_sub_link_url = urljoin(policy_url, sub_link_url)
            
            # Prevent self-referencing loops
            if full_sub_link_url.rstrip('/') != policy_url.rstrip('/'):
                logger.info(f"Performing DPO Multi-Hop (Hop 2): {full_sub_link_url}")
                await page.goto(full_sub_link_url, timeout=60000)
                sub_link_html = await page.content()
                
                # Re-run DPO analysis on the new page
                second_dpo_output = await analyzer.find_dpo(sub_link_html, full_sub_link_url)
                dpo_output = second_dpo_output # Overwrite with the more specific result
            else:
                logger.warning(f"Multi-Hop skipped: Sub-link is same as current page: {policy_url}")
        
        await page.close()
        return dpo_output, retention_output

    except Exception as e:
        logger.error(f"Error analyzing privacy page {policy_url}: {e}")
        if page:
            await page.close()
        dpo_output_error = {"dpo_found": False, "email_address": None, "postal_address": None, "sub_link": None, "reasoning": f"Failed during privacy page analysis: {e}"}
        retention_output_error = {"retention_found": False, "retention_policy_summary": None, "reasoning": f"Failed during privacy page analysis: {e}"}
        return dpo_output_error, retention_output_error


async def process_site_scenario(browser, analyzer: PrivacyAnalyzer, site_url: str, scenario: str) -> Dict[str, Any]:
    """
    Runs the full analysis for a single site and a single cookie scenario.
    Returns a dictionary of results.
    """
    logger.info(f"Processing: {site_url} (Scenario: {scenario})")
    page = None
    try:
        page = await browser.new_page()
        
        # --- 1. Navigation and Cookie Handling ---
        await page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
        await handle_cookie_banner(page, action=scenario)
        await page.wait_for_timeout(3000) # Wait for actions to apply
        
        cookies = await page.context.cookies()
        logger.info(f"Captured {len(cookies)} cookies for {site_url} ('{scenario}').")

        # --- 2. Cookie Analysis ---
        cookie_categories = await analyzer.categorize_cookies(cookies)
        
        base_domain = urlparse(site_url).netloc.replace('www.', '')
        third_party_count = sum(1 for c in cookies if not c['domain'].replace('.', '').endswith(base_domain))
        logger.info(f"Found {third_party_count} third-party cookies.")
        
        # --- 3. Find Privacy Policy ---
        html_content = await page.content()
        simple_extractor(html_content) # Assuming this is a local utility
        
        llm_output = await analyzer.find_privacy_policy(html_content, site_url)
        logger.info(f"Privacy policy search for {site_url} complete.")

        # --- 4. DPO & Retention Analysis (if policy found) ---
        dpo_output = {"dpo_found": False, "email_address": None, "postal_address": None, "sub_link": None, "reasoning": "No privacy policy URL found."}
        retention_output = {"retention_found": False, "retention_policy_summary": None, "reasoning": "No privacy policy URL found."}

        if llm_output.get("result_found"):
            policy_url_path = llm_output.get("privacy_policy_url")
            full_privacy_policy_url = urljoin(site_url, policy_url_path)
            
            dpo_output, retention_output = await analyze_privacy_policy_page(
                browser, analyzer, full_privacy_policy_url
            )
            
        await page.close()
        
        # --- 5. Format Success Result ---
        return {
            "website_url": site_url,
            "scenario": scenario,
            "privacy_policy_url": llm_output.get("privacy_policy_url"),
            "llm_found": llm_output.get("result_found"),
            "llm_reasoning": llm_output.get("reasoning"),
            "dpo_email": dpo_output.get("email_address"),
            "dpo_address": dpo_output.get("postal_address"),
            "dpo_found": dpo_output.get("dpo_found"),
            "dpo_reasoning": dpo_output.get("reasoning"),
            "retention_policy_summary": retention_output.get("retention_policy_summary"),
            "retention_found": retention_output.get("retention_found"),
            "retention_reasoning": retention_output.get("reasoning"),
            "cookies_count": len(cookies),
            "third_party_cookies_count": third_party_count,
            "raw_cookies_data": json.dumps(cookies),
            "categorized_cookies": json.dumps(cookie_categories)
        }

    except Exception as e:
        logger.error(f"FATAL Error processing {site_url} ('{scenario}'): {e}")
        if page:
            await page.close()
        # --- 6. Format Error Result ---
        return {
            "website_url": site_url,
            "scenario": scenario,
            "privacy_policy_url": "N/A",
            "llm_found": False,
            "llm_reasoning": f"Failed to process: {e}",
            "dpo_email": "N/A",
            "dpo_address": "N/A",
            "dpo_found": False,
            "dpo_reasoning": f"Failed to process: {e}",
            "retention_policy_summary": "N/A",
            "retention_found": False,
            "retention_reasoning": f"Failed to process: {e}",
            "cookies_count": 0,
            "third_party_cookies_count": 0,
            "raw_cookies_data": "[]",
            "categorized_cookies": "{}"
        }


async def run_all_analyses(sites_df: pd.DataFrame, analyzer: PrivacyAnalyzer, browser) -> List[Dict[str, Any]]:
    """
    Creates and runs all analysis tasks concurrently.
    """
    tasks = []
    scenarios = ["accept", "reject"]

    for index, row in sites_df.iterrows():
        site_url = row['website_url']
        if not site_url.startswith('http://') and not site_url.startswith('https://'):
            site_url = 'https://' + site_url
            
        for scenario in scenarios:
            # Create a task for each site/scenario combination
            tasks.append(
                process_site_scenario(browser, analyzer, site_url, scenario)
            )
    
    # Run all tasks concurrently and gather results
    results = await asyncio.gather(*tasks) # unpack tasks
    return results


def save_results(results: List[Dict[str, Any]]):
    """
    Saves the list of result dictionaries to a timestamped JSON file.
    """
    results_df = pd.DataFrame(results)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"analysis_results_{timestamp}.json"
    results_df.to_json(filename, orient="records", indent=4)
    logger.info(f"Analysis complete. Results saved to {filename}")


async def gdpr_analysis(sites_df):
    """
    Orchestrates the setup, execution, and saving of the analysis.
    """
    llm_provider = OllamaProvider(model='llama3')
    analyzer = PrivacyAnalyzer(llm_client=llm_provider)
    
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        all_results = await run_all_analyses(sites_df, analyzer, browser)
        
        await browser.close()
    
    save_results(all_results)


def main():
    setup_logging()
    logger.info("Starting GDPR Cookie Analysis...")

    if len(sys.argv) > 1:
        site_url_from_cli = sys.argv[1]
        logger.info(f"Processing single URL from command line: {site_url_from_cli}")
        sites_df = pd.DataFrame([{'website_url': site_url_from_cli}])
    else:
        try:
            logger.info("Loading URLs from sites.csv...")
            sites_df = pd.read_csv("sites.csv", header=None, names=['index_col', 'website_url'])
            sites_df = sites_df.drop(columns=['index_col'])
            logger.info(f"Loaded {len(sites_df)} sites from CSV.")
        except FileNotFoundError:
            logger.error("No URL provided and 'sites.csv' file not found.")
            logger.error("Usage: poetry run main <your_url> OR create 'sites.csv'")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error reading sites.csv: {e}")
            sys.exit(1)

    asyncio.run(gdpr_analysis(sites_df))

if __name__ == "__main__":
    main()
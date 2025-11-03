import pandas as pd
import json
import asyncio
import sys
import logging
from playwright.async_api import async_playwright
from datetime import datetime
from urllib.parse import urlparse, urljoin
from typing import List, Dict, Any, Optional
from dataclasses import asdict

# Import relativi (presupponendo la struttura del progetto)
from .utils.logging_setup import *
from .utils.cookie_helpers import simplify_cookies, count_third_party_cookies
from .analysis.scraper import handle_cookie_banner, simple_extractor
from .analysis.ollama_providers import OllamaProvider
from .analysis.privacy_analyzers import PrivacyAnalyzer
from .analysis.llm_interface import AbstractLLMClient
from .analysis.models import SiteAnalysisResult

logger = logging.getLogger(__name__)


async def process_site_scenario(browser, analyzer: PrivacyAnalyzer, site_url: str, scenario: str) -> SiteAnalysisResult:
    """
    Runs the full analysis for a single site and a single cookie scenario.
    Returns a SiteAnalysisResult object.
    """
    logger.info(f"Processing: {site_url} (Scenario: {scenario})")
    try:
        async with await browser.new_page() as page:
            # --- 1. Navigation and Cookie Handling ---
            await page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
            await handle_cookie_banner(page, action=scenario)
            await page.wait_for_timeout(3000)  # Wait for actions to apply

            # Get the final URL after potential redirects from navigation or cookie banners
            current_url = page.url
            logger.info(f"Final URL after navigation: {current_url}")

            cookies = await page.context.cookies()
            logger.info(f"[{scenario}] Captured {len(cookies)} cookies for {current_url}.")

            # --- 2. Cookie Analysis ---
            logger.debug(f"Cookies content: {cookies}")
            simplified_cookies = simplify_cookies(cookies)

            logger.debug("Categorizing cookies...")
            cookie_categories = await analyzer.categorize_cookies(simplified_cookies)

            third_party_count = count_third_party_cookies(current_url, cookies)

            # --- 3. Find Privacy Policy ---
            logger.debug("Getting page content for simple extractor...")
            html_content = await page.content()
            simple_links = simple_extractor(html_content)
            logger.info(f"[{scenario}] Simple extractor found links: {simple_links}")

            # This single call now handles the initial search, deep search, and finds the best policy.
            llm_output = await analyzer.find_privacy_policy(page)

            # --- 4. DPO & Retention Analysis (if policy found) ---
            analyses_results = {}
            full_privacy_policy_url = None
            
            if llm_output.get("privacy_policy_url"):
                policy_url_path = llm_output.get("privacy_policy_url")
                full_privacy_policy_url = urljoin(current_url, policy_url_path)

                # Define tasks for parallel execution
                # dpo_task = asyncio.create_task(analyzer.find_dpo(
                #     browser, full_privacy_policy_url
                # ))
                # retention_task = asyncio.create_task(analyzer.analyze_retention_policy(
                #     browser, full_privacy_policy_url
                # ))
                # cookie_declaration_task = asyncio.create_task(analyzer.find_cookie_declaration_page(
                #     browser, full_privacy_policy_url
                # ))
                # deletion_page_task = asyncio.create_task(analyzer.find_data_deletion_page(
                #     browser, full_privacy_policy_url
                # ))
                
                # Run tasks and gather results
                # deletion_res  = await asyncio.gather(
                #     deletion_page_task 
                # )
                # cookie_decl_res, deletion_res, retention_res, dpo_res  = await asyncio.gather(
                #     cookie_declaration_task, deletion_page_task retention_task, dpo_task, 
                # )
                
                # Collect results into the extensible dictionary
                analyses_results = {
                    # "cookie_declaration": cookie_decl_res,
                    # "data_deletion": deletion_res,
                    # "retention": retention_res,
                    # "dpo": dpo_res,
                }

            # --- 5. Format Success Result ---
            return SiteAnalysisResult.from_outputs(
                site_url=current_url,
                scenario=scenario,
                cookies=cookies,
                cookie_categories=cookie_categories,
                third_party_count=third_party_count,
                llm_output=llm_output,
                privacy_policy_url=full_privacy_policy_url,
                simple_extractor_links=simple_links,
                **analyses_results
            )

    except Exception as e:
        logger.error(f"FATAL Error processing {site_url} ('{scenario}'): {e}")
        return SiteAnalysisResult.from_exception(site_url, scenario, e)

async def run_all_analyses(sites_df: pd.DataFrame, analyzer: PrivacyAnalyzer, browser) -> List[SiteAnalysisResult]:
    """
    Creates and runs all analysis tasks concurrently.
    """
    tasks = []
    # scenarios = ["accept", "reject"]
    scenarios = ["accept"]

    for index, row in sites_df.iterrows():
        site_url = row['website_url']
        parsed_url = urlparse(site_url)
        if not parsed_url.scheme:
            site_url = "https://" + site_url
            
        for scenario in scenarios:
            tasks.append(
                process_site_scenario(browser, analyzer, site_url, scenario)
            )
    
    results = await asyncio.gather(*tasks)
    return results


def save_results(results: List[SiteAnalysisResult]):
    """
    Saves the list of result dataclasses to a timestamped JSON file.
    """
    results_dicts = [asdict(result) for result in results]
    results_df = pd.DataFrame(results_dicts)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"output/analysis_results_{timestamp}.json"
    results_df.to_json(filename, orient="records", indent=4)
    logger.info(f"Analysis complete. Results saved to {filename}")


def load_llm_config():
    """
    Loads LLM configuration from config.json.
    """
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        return config['llm']
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Could not load LLM config from config.json: {e}. Using default.")
        return {"model": "llama3"}


def load_scraper_config():
    """
    Loads scraper configuration from config.json.
    """
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        return config['scraper']
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Could not load scraper config from config.json: {e}. Using default.")
        return {"max_hops": 3}


async def gdpr_analysis(sites_df):
    """
    Orchestrates the setup, execution, and saving of the analysis.
    """
    llm_config = load_llm_config()
    scraper_config = load_scraper_config()
    
    llm_provider = OllamaProvider(model=llm_config.get('model', 'llama3'))
    analyzer = PrivacyAnalyzer(
        llm_client=llm_provider,
        max_hops=scraper_config.get('max_hops', 3)
    )
    
    
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
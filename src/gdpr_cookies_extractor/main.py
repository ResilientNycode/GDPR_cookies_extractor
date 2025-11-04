import pandas as pd
import json
import asyncio
import sys
import logging
import re
import os
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


def sanitize_filename(url: str) -> str:
    """Sanitizes a URL to be used as a valid filename."""
    parsed_url = urlparse(url)
    
    sanitized = re.sub(r'[\/*?:"<>|]', "_", parsed_url.netloc)
    return sanitized


async def dump_data(current_url: str, scenario: str, cookies: list, browser, full_privacy_policy_url: Optional[str], timestamp: str):
    """Dumps cookies to a JSON file and the privacy policy to an HTML file."""
    sanitized_url = sanitize_filename(current_url)
    dump_dir = f"output/dumps/analysis_results_{timestamp}"
    os.makedirs(dump_dir, exist_ok=True)
    
    # Dump cookies
    cookie_dump_path = f"{dump_dir}/{sanitized_url}_{scenario}_cookies.json"
    with open(cookie_dump_path, "w") as f:
        json.dump(cookies, f, indent=4)
    logger.info(f"Dumped {len(cookies)} cookies to {cookie_dump_path}")

    # Dump privacy policy
    if full_privacy_policy_url:
        try:
            async with await browser.new_page() as policy_page:
                await policy_page.goto(full_privacy_policy_url, wait_until="domcontentloaded", timeout=60000)
                policy_html = await policy_page.content()
                policy_dump_path = f"{dump_dir}/{sanitized_url}_{scenario}_privacy_policy.html"
                with open(policy_dump_path, "w", encoding="utf-8") as f:
                    f.write(policy_html)
                logger.info(f"Dumped privacy policy to {policy_dump_path}")
        except Exception as e:
            logger.error(f"Failed to dump privacy policy for {full_privacy_policy_url}: {e}")


async def process_site_scenario(browser, analyzer: PrivacyAnalyzer, site_url: str, scenario: str, timestamp: str) -> SiteAnalysisResult:
    """
    Runs the full analysis for a single site and a single cookie scenario.
    Returns a SiteAnalysisResult object.
    """
    logger.info(f"Processing: {site_url} (Scenario: {scenario})")
    try:
        async with await browser.new_page() as page:
            # Navigation and Cookie Handling ---
            await page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
            await handle_cookie_banner(page, action=scenario)
            await page.wait_for_timeout(3000)  # Give the page time to process the click

            # Get the final URL after potential redirects from navigation or cookie banners
            current_url = page.url
            logger.info(f"Final URL after navigation: {current_url}")

            cookies = await page.context.cookies()
            logger.info(f"[{scenario}] Captured {len(cookies)} cookies for {current_url}.")

            # Cookie Analysis ---
            logger.debug(f"Cookies content: {cookies}")
            simplified_cookies = simplify_cookies(cookies)

            logger.debug("Categorizing cookies...")
            cookie_categories = await analyzer.categorize_cookies(simplified_cookies)

            third_party_count = count_third_party_cookies(current_url, cookies)

            # Find Privacy Policy ---
            logger.debug("Getting page content for simple extractor...")
            html_content = await page.content()
            simple_links = simple_extractor(html_content)
            logger.info(f"[{scenario}] Simple extractor found links: {simple_links}")

            llm_output = await analyzer.find_privacy_policy(page)

            # DPO & Retention Analysis (if policy found) ---
            analyses_results = {}
            full_privacy_policy_url = None
            
            if llm_output.get("privacy_policy_url"):
                policy_url_path = llm_output.get("privacy_policy_url")
                full_privacy_policy_url = urljoin(current_url, policy_url_path)

            await dump_data(current_url, scenario, cookies, browser, full_privacy_policy_url, timestamp)
            

            if full_privacy_policy_url:
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

            # Format Success Result ---
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

async def run_all_analyses(sites_df: pd.DataFrame, analyzer: PrivacyAnalyzer, browser, timestamp: str) -> List[SiteAnalysisResult]:
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
                process_site_scenario(browser, analyzer, site_url, scenario, timestamp)
            )
    
    results = await asyncio.gather(*tasks)
    return results


def save_results(results: List[SiteAnalysisResult], timestamp: str):
    """
    Saves the list of result dataclasses to a timestamped JSON file.
    """
    results_dicts = [asdict(result) for result in results]
    logger.debug(f"Data to be serialized: {results_dicts}") 
    filename = f"output/analysis_results_{timestamp}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results_dicts, f, indent=4, ensure_ascii=False)
    logger.info(f"Analysis complete. Results saved to {filename}")


def create_output_directories():
    """
    Creates the necessary output directories if they don't already exist.
    """
    os.makedirs("output", exist_ok=True)
    os.makedirs("output/dumps", exist_ok=True)
    logger.info("Ensured output directories exist.")


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
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    
    llm_provider = OllamaProvider(model=llm_config.get('model', 'llama3'))
    analyzer = PrivacyAnalyzer(
        llm_client=llm_provider,
        max_hops=scraper_config.get('max_hops', 3)
    )
    
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        all_results = await run_all_analyses(sites_df, analyzer, browser, timestamp)
        
        await browser.close()
    
    save_results(all_results, timestamp)


def main():
    setup_logging()
    logger.info("Starting GDPR Cookie Analysis...")

    create_output_directories()

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
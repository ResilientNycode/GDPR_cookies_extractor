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

# Relative imports
from .utils.logging_setup import setup_logging
from .utils.cookie_helpers import simplify_cookies, count_third_party_cookies
from .analysis.scraper import handle_cookie_banner, extract_links
from .analysis.ollama_providers import OllamaProvider
from .analysis.privacy_analyzers import PrivacyAnalyzer
from .analysis.models import SiteAnalysisResult

logger = logging.getLogger(__name__)


def sanitize_filename(url: str) -> str:
    """Sanitizes a URL to be used as a valid filename."""
    parsed_url = urlparse(url)
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", parsed_url.netloc)
    return sanitized

async def dump_html_content(page, scenario: str, timestamp: str):
    """Dumps the HTML content of the current page."""
    current_url = page.url
    sanitized_url = sanitize_filename(current_url)
    dump_dir = f"output/dumps/analysis_results_{timestamp}"
    os.makedirs(dump_dir, exist_ok=True)
    
    try:
        html_content = await page.content()
        dump_path = f"{dump_dir}/{sanitized_url}_{scenario}_page.html"
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"Dumped HTML content to {dump_path}")
    except Exception as e:
        logger.error(f"Failed to dump HTML content for {current_url}: {e}")

async def process_site_scenario(browser, analyzer: PrivacyAnalyzer, site_url: str, scenario: str, timestamp: str, user_keywords_config: Dict[str, List[str]]) -> SiteAnalysisResult:
    """
    Runs the full analysis for a single site and a single cookie scenario.
    """
    logger.info(f"Processing: {site_url} (Scenario: {scenario})")
    page = None
    try:
        page = await browser.new_page()
        await page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
        await handle_cookie_banner(page, action=scenario)
        await page.wait_for_timeout(3000)

        current_url = page.url
        logger.info(f"Final URL after navigation: {current_url}")

        cookies = await page.context.cookies()
        simplified_cookies = simplify_cookies(cookies)
        cookie_categories = await analyzer.categorize_cookies(simplified_cookies)
        third_party_count = count_third_party_cookies(current_url, cookies)
        
        # This is a simplified version of link extraction for context
        simple_links = await extract_links(page, current_url)

        # --- Main Analysis Flow ---
        analyses_results = {}

        # 1. Find Privacy Policy
        policy_result = await analyzer.find_privacy_policy(
            browser, current_url, user_keywords=user_keywords_config.get('privacy_policy', [])
        )
        analyses_results["privacy_policy"] = policy_result
        privacy_policy_url = policy_result.get("privacy_policy_url")

        # 2. Analyze from Privacy Policy if found
        if privacy_policy_url:
            logger.info(f"Privacy Policy found at {privacy_policy_url}. Starting secondary analysis.")
            cookie_task = analyzer.find_cookie_declaration(
                browser, privacy_policy_url, user_keywords=user_keywords_config.get('cookie_declaration', [])
            )
            deletion_task = analyzer.find_data_deletion_info(
                browser, privacy_policy_url, user_keywords=user_keywords_config.get('data_deletion', [])
            )
            
            cookie_res, deletion_res = await asyncio.gather(cookie_task, deletion_task)
            
            analyses_results["cookie_declaration"] = cookie_res
            analyses_results["data_deletion"] = deletion_res
        else:
            logger.warning(f"Could not find privacy policy for {current_url}. Skipping secondary analysis.")

        await dump_html_content(page, scenario, timestamp)

        return SiteAnalysisResult(
            website_url=current_url,
            scenario=scenario,
            cookies_count=len(cookies),
            third_party_cookies_count=third_party_count,
            raw_cookies_data=cookies,
            categorized_cookies=cookie_categories.get("cookie_categories", []),
            simple_extractor_links=simple_links,
            analyses=analyses_results
        )

    except Exception as e:
        logger.error(f"FATAL Error processing {site_url} ('{scenario}'): {e}", exc_info=True)
        return SiteAnalysisResult.from_exception(site_url, scenario, e)
    finally:
        if page:
            await page.close()

async def run_all_analyses(sites_df: pd.DataFrame, analyzer: PrivacyAnalyzer, browser, timestamp: str, user_keywords_config: Dict[str, List[str]]) -> List[SiteAnalysisResult]:
    tasks = []
    scenarios = ["accept"]

    for _, row in sites_df.iterrows():
        site_url = row['website_url']
        if not urlparse(site_url).scheme:
            site_url = "https://" + site_url
            
        for scenario in scenarios:
            tasks.append(
                process_site_scenario(browser, analyzer, site_url, scenario, timestamp, user_keywords_config)
            )
    
    return await asyncio.gather(*tasks)

def save_results(results: List[SiteAnalysisResult], timestamp: str):
    results_dicts = [asdict(result) for result in results]
    filename = f"output/analysis_results_{timestamp}.json"
    os.makedirs("output", exist_ok=True)
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results_dicts, f, indent=4, ensure_ascii=False)
    logger.info(f"Analysis complete. Results saved to {filename}")

def load_config(key: str, default: Any) -> Any:
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        return config.get(key, default)
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Could not load '{key}' from config.json: {e}. Using default.")
        return default

async def main_async():
    setup_logging()
    logger.info("Starting GDPR Cookie Analysis...")

    os.makedirs("output/dumps", exist_ok=True)

    if len(sys.argv) > 1:
        sites_df = pd.DataFrame([{'website_url': sys.argv[1]}])
    else:
        try:
            sites_df = pd.read_csv("sites.csv", header=None, names=['index_col', 'website_url'])
            sites_df = sites_df.drop(columns=['index_col'])
        except FileNotFoundError:
            logger.error("'sites.csv' not found. Please create it or provide a URL as an argument.")
            return

    llm_config = load_config('llm', {"model": "llama3"})
    scraper_config = load_config('scraper', {"max_hops": 3})
    user_keywords_config = load_config('user_defined_keywords', {})
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    
    llm_provider = OllamaProvider(model=llm_config.get('model', 'llama3'))
    analyzer = PrivacyAnalyzer(
        llm_client=llm_provider,
        max_hops=scraper_config.get('max_hops', 3)
    )
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        all_results = await run_all_analyses(sites_df, analyzer, browser, timestamp, user_keywords_config)
        await browser.close()
    
    save_results(all_results, timestamp)

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
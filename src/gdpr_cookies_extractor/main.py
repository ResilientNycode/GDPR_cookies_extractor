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
from .analysis.scraper import handle_cookie_banner, simple_extractor
from .analysis.ollama_providers import OllamaProvider
from .analysis.privacy_analyzers import PrivacyAnalyzer
from .analysis.llm_interface import AbstractLLMClient
from .analysis.models import SiteAnalysisResult

logger = logging.getLogger(__name__)


async def analyze_privacy_policy_page(browser, analyzer: PrivacyAnalyzer, policy_url: str, scenario: str) -> Dict[str, Any]:
    """
    Navigates to a privacy policy page and analyzes it for retention info.
    """
    final_retention_output = {}

    try:
        initial_page = await browser.new_page()
        await initial_page.goto(policy_url, timeout=60000)
        privacy_policy_html = await initial_page.content()

        logger.info(f"[{scenario}] Analyzing for data retention: {policy_url}")
        final_retention_output = await analyzer.analyze_retention_policy(privacy_policy_html, policy_url)
        await initial_page.close()

        return final_retention_output

    except Exception as e:
        logger.error(f"[{scenario}] Error analyzing privacy page {policy_url}: {e}")
        retention_output_error = {"reasoning": f"Failed during privacy page analysis: {e}"}
        # Correzione: ritorna il dizionario di errore
        return retention_output_error
        # Il codice originale aveva un grave errore di nesting qui


async def process_site_scenario(browser, analyzer: PrivacyAnalyzer, site_url: str, scenario: str) -> SiteAnalysisResult:
    """
    Runs the full analysis for a single site and a single cookie scenario.
    Returns a SiteAnalysisResult object.
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
        logger.info(f"[{scenario}] Captured {len(cookies)} cookies for {site_url}.")

        # --- 2. Cookie Analysis ---
        cookie_categories = await analyzer.categorize_cookies(cookies)
        
        base_domain = urlparse(site_url).netloc.replace('www.', '')
        third_party_count = sum(1 for c in cookies if not c['domain'].replace('.', '').endswith(base_domain))
        logger.info(f"[{scenario}] Found {third_party_count} third-party cookies.")
        
        # --- 3. Find Privacy Policy ---
        html_content = await page.content()
        
        # Simple extractor for comparison
        simple_links = simple_extractor(html_content)
        logger.info(f"[{scenario}] Simple extractor found links: {simple_links}")

        llm_output = await analyzer.find_privacy_policy(html_content, site_url)
        logger.info(f"[{scenario}] Initial privacy policy search for {site_url} complete.")

        found_policies = []
        if llm_output.get("privacy_policy_url"):
            found_policies.append(llm_output)

        if not llm_output.get("privacy_policy_url"):
            logger.info(f"[{scenario}] Privacy policy not found on main page. Searching internal links...")
            internal_links = await analyzer._get_internal_links(page, site_url)
            
            promising_keywords = ['privacy', 'legal', 'terms', 'imprint', 'about', 'contact']
            promising_links = [
                link for link in internal_links 
                if any(keyword in link.lower() for keyword in promising_keywords)
            ]
            
            for link in promising_links:
                try:
                    logger.info(f"[{scenario}] Navigating to promising link: {link}")
                    await page.goto(link, wait_until="domcontentloaded", timeout=30000)
                    secondary_html = await page.content()
                    secondary_output = await analyzer.find_privacy_policy(secondary_html, link)
                    if secondary_output.get("privacy_policy_url"):
                        logger.info(f"[{scenario}] Found potential privacy policy at: {link}")
                        found_policies.append(secondary_output)
                except Exception as e:
                    logger.warning(f"[{scenario}] Could not analyze promising link {link}: {e}")
                    continue
        
        if len(found_policies) > 0:
            llm_output = max(found_policies, key=lambda x: x.get('confidence_score', 0.0))
            logger.info(f"[{scenario}] Selected best privacy policy with score {llm_output.get('confidence_score')}: {llm_output.get('privacy_policy_url')}")

        # --- 4. DPO & Retention Analysis (if policy found) ---
        dpo_output = {"reasoning": "No privacy policy URL found."}
        retention_output = {"reasoning": "No privacy policy URL found."}

        if llm_output.get("privacy_policy_url"):
            policy_url_path = llm_output.get("privacy_policy_url")
            full_privacy_policy_url = urljoin(site_url, policy_url_path)
            
            # Esegui l'analisi della DPO e della retention in parallelo
            dpo_task = asyncio.create_task(analyzer.find_dpo(
                browser, full_privacy_policy_url, scenario
            ))
            
            retention_task = asyncio.create_task(analyze_privacy_policy_page(
                browser, analyzer, full_privacy_policy_url, scenario
            ))
            
            dpo_output, retention_output = await asyncio.gather(dpo_task, retention_task)
            
        await page.close()
        
        # --- 5. Format Success Result ---
        return SiteAnalysisResult.from_outputs(
            site_url=site_url,
            scenario=scenario,
            cookies=cookies,
            cookie_categories=cookie_categories,
            third_party_count=third_party_count,
            llm_output=llm_output,
            dpo_output=dpo_output,
            retention_output=retention_output
        )

    except Exception as e:
        logger.error(f"FATAL Error processing {site_url} ('{scenario}'): {e}")
        if page:
            await page.close()
        return SiteAnalysisResult.from_exception(site_url, scenario, e)


async def run_all_analyses(sites_df: pd.DataFrame, analyzer: PrivacyAnalyzer, browser) -> List[SiteAnalysisResult]:
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
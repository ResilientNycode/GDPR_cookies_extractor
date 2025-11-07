import logging
import json
from playwright.async_api import Page
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from typing import List

logger = logging.getLogger(__name__)

def load_selectors_from_config():
    """Loads cookie banner selectors from config.json."""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        return config['scraper']['cookie_banners']
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"Error loading selectors from config.json: {e}")
        # Fallback to default selectors if config is invalid or not found
        return {
            "accept_selectors": [
                "text=Accept", "text=Accept All", "text=OK",
                "role=button[name='Accept']", "role=button[name='Accept All']", "role=button[name='OK']"
            ],
            "reject_selectors": [
                "text=Reject", "text=Reject All", "text=Deny",
                "role=button[name='Reject']", "role=button[name='Reject All']", "role=button[name='Deny']"
            ]
        }

async def handle_cookie_banner(page: Page, action="accept"):
    """
    Finds and clicks the cookie banner button based on the desired action.
    """
    selectors_config = load_selectors_from_config()
    accept_selectors = selectors_config.get("accept_selectors", [])
    reject_selectors = selectors_config.get("reject_selectors", [])

    target_selectors = accept_selectors if action == "accept" else reject_selectors

    for selector in target_selectors:
        try:
            # Use a short timeout to quickly check for each selector
            button = page.locator(selector).first
            await button.wait_for(state='visible', timeout=2000)
            logger.info(f"Clicking '{action}' button with selector: {selector}")
            await button.click(timeout=5000)
            # Wait for a moment to let the action complete (e.g., banner disappears)
            await page.wait_for_timeout(2000)
            return True
        except Exception:
            # This is expected if a selector doesn't match; just try the next one.
            logger.debug(f"Selector '{selector}' not found or failed, trying next.")
            continue
    
    logger.info(f"No '{action}' button found or all attempts failed.")
    return False

async def extract_links(page: Page, page_url: str) -> List[str]:
    """
    Extracts all internal links from a page's HTML, correctly resolving relative URLs
    using the <base> tag if present.
    """
    try:
        html_content = await page.content()
        soup = BeautifulSoup(html_content, "html.parser")

        # 1. Determine the base URL
        base_tag = soup.find("base", href=True)
        base_url = urljoin(page_url, base_tag["href"]) if base_tag else page_url
        logger.info(f"Using base URL for link resolution: {base_url}")

        # 2. Determine the main domain for internal link checking
        parsed_page_url = urlparse(page_url)
        domain_parts = parsed_page_url.netloc.split('.')
        main_domain = '.'.join(domain_parts[-2:]) if len(domain_parts) > 2 else parsed_page_url.netloc

        links = set()
        # 3. Find all links and resolve them
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href:
                # Resolve the URL against the (potentially new) base URL
                full_url = urljoin(base_url, href)
                
                # 4. Filter for internal links (including subdomains)
                try:
                    parsed_full_url = urlparse(full_url)
                    # Check if the link's domain ends with the main domain
                    if parsed_full_url.netloc.endswith(main_domain):
                        # Exclude anchors and common non-html files
                        if '#' not in full_url and not full_url.lower().endswith(('.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.pdf', '.svg')):
                            links.add(full_url)
                except Exception:
                    logger.warning(f"Could not parse resolved URL: {full_url}. Skipping.")

        logger.info(f"Extractor found {len(links)} internal links on {page_url}.")
        return list(links)

    except Exception as e:
        logger.error(f"Failed to extract links from {page_url}: {e}")
        return []


async def get_page_content(page: Page, url: str):
    """
    Navigates to a URL and returns the complete HTML content after JavaScript execution.
    """
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000) # Extra wait for any lazy-loading scripts
    html_content = await page.content()
    return html_content

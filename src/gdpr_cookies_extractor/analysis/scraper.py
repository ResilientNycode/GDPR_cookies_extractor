import logging
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

async def handle_cookie_banner(page, action="accept"):
    """
    Finds and clicks the cookie banner button based on the desired action.
    """
    # Define common selectors for "accept" and "reject" buttons.
    # Playwright's locators (e.g., page.get_by_text) are powerful for this.
    # 
    accept_selectors = [
        "text=Accept",
        "text=Accept All",
        "text=OK",
        "role=button[name='Accept']",
        "role=button[name='Accept All']",
        "role=button[name='OK']"
    ]
    reject_selectors = [
        "text=Reject",
        "text=Reject All",
        "text=Deny",
        "role=button[name='Reject']",
        "role=button[name='Reject All']",
        "role=button[name='Deny']"
    ]

    target_selectors = accept_selectors if action == "accept" else reject_selectors

    for selector in target_selectors:
        try:
            button = page.locator(selector)
            
            # Check if the button is visible with a timeout. Playwright will automatically
            # wait for the element to appear before giving up.
            if await button.is_visible(timeout=5000):
                logger.info(f"Clicking '{action}' button with selector: {selector}")
                await button.click()
                await page.wait_for_timeout(2000) # Give the page time to process the click
                return True
        except Exception:
            # Continue to the next selector if this one fails
            continue
    
    logger.info(f"No '{action}' button found for this site.")
    return False

def simple_extractor(html_page):
    """
    A simple rule-based function to find privacy-related links using BeautifulSoup.
    """
    # Beautiful Soup is a Python library for parsing HTML and XML documents.
    # It creates a parse tree that can be used to extract data.
    # "html.parser" is Python's built-in parser for this task.
    soup = BeautifulSoup(html_page, "html.parser")

    privacy_links = []
    # Find all anchor tags that have an 'href' attribute.
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        # get_text() extracts the visible text, and strip=True removes whitespace.
        text = a.get_text(strip=True).lower()
        if "privacy" in href or "privacy" in text:
            privacy_links.append(a["href"])

    privacy_links = list(set(privacy_links))

    logger.info("Privacy related links:")
    for link in privacy_links:
        logger.info(link)

async def get_page_content(page, url):
    """
    Navigates to a URL and returns the complete HTML content after JavaScript execution.
    """
    # The page.content() method returns the full HTML source after JavaScript has run,
    # which is ideal for scraping dynamic content.
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    html_content = await page.content()
    return html_content
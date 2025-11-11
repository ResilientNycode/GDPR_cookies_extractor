import httpx
import xml.etree.ElementTree as ET
import logging
from urllib.parse import urljoin, urlparse
from typing import List, Set, Optional

logger = logging.getLogger(__name__)

# Standard XML namespaces for sitemaps
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

async def _fetch_url_content(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Fetches content from a URL."""
    try:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        return response.text
    except httpx.RequestError as e:
        logger.warning(f"HTTP request failed for {url}: {e}")
        return None
    except httpx.HTTPStatusError as e:
        logger.warning(f"HTTP status error for {url}: {e.response.status_code}")
        return None

async def _parse_sitemap_xml(xml_content: str, visited_sitemaps: Set[str]) -> List[str]:
    """Parses a single sitemap.xml file and returns a list of URLs, handling nested sitemaps."""
    urls = []
    try:
        root = ET.fromstring(xml_content)
        
        # Check if it's a sitemap index
        if root.tag == f"{{{SITEMAP_NS}}}sitemapindex":
            logger.info("Sitemap index found. Parsing nested sitemaps.")
            # This is a sitemap index file, parse it for other sitemaps
            for sitemap in root.findall(f"{{{SITEMAP_NS}}}sitemap"):
                loc_element = sitemap.find(f"{{{SITEMAP_NS}}}loc")
                if loc_element is not None and loc_element.text:
                    nested_sitemap_url = loc_element.text.strip()
                    if nested_sitemap_url not in visited_sitemaps:
                        urls.append(nested_sitemap_url) # Add to be processed later
        
        # Check if it's a regular URL set
        elif root.tag == f"{{{SITEMAP_NS}}}urlset":
            # This is a standard sitemap file, parse it for URLs
            for url in root.findall(f"{{{SITEMAP_NS}}}url"):
                loc_element = url.find(f"{{{SITEMAP_NS}}}loc")
                if loc_element is not None and loc_element.text:
                    urls.append(loc_element.text.strip())
    except ET.ParseError:
        logger.warning("Failed to parse XML content.")
    
    return urls

async def get_sitemap_urls(site_url: str) -> List[str]:
    """
    Finds and parses a website's sitemap(s) to extract all unique URLs.

    This function first checks robots.txt for a sitemap location, falls back to
    /sitemap.xml, and recursively parses sitemap indexes.

    Args:
        site_url: The base URL of the website.

    Returns:
        A list of all unique URLs found in the sitemap(s).
    """
    base_url = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
    robots_url = urljoin(base_url, "/robots.txt")
    sitemap_urls_to_process = []
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Check robots.txt for sitemap
        logger.info(f"Checking for sitemap in {robots_url}")
        robots_content = await _fetch_url_content(client, robots_url)
        if robots_content:
            for line in robots_content.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_url = line.split(":", 1)[1].strip()
                    sitemap_urls_to_process.append(sitemap_url)
                    logger.info(f"Found sitemap in robots.txt: {sitemap_url}")

        # 2. If not in robots.txt, try default /sitemap.xml
        if not sitemap_urls_to_process:
            default_sitemap_url = urljoin(base_url, "/sitemap.xml")
            logger.info(f"No sitemap in robots.txt, trying default: {default_sitemap_url}")
            sitemap_urls_to_process.append(default_sitemap_url)

        # 3. Process all found sitemaps (including nested ones)
        final_urls = set()
        visited_sitemaps = set()
        
        while sitemap_urls_to_process:
            sitemap_url = sitemap_urls_to_process.pop(0)
            if sitemap_url in visited_sitemaps:
                continue
            
            visited_sitemaps.add(sitemap_url)
            logger.info(f"Processing sitemap: {sitemap_url}")
            
            sitemap_content = await _fetch_url_content(client, sitemap_url)
            if sitemap_content:
                parsed_items = await _parse_sitemap_xml(sitemap_content, visited_sitemaps)
                
                # The parser returns a mix of page URLs and nested sitemap URLs.
                # We need to distinguish them. A simple heuristic is checking the extension.
                for item in parsed_items:
                    if item.endswith('.xml'):
                        if item not in visited_sitemaps:
                            sitemap_urls_to_process.append(item)
                    else:
                        final_urls.add(item)

    logger.info(f"Found {len(final_urls)} unique page URLs from sitemap(s).")
    return list(final_urls)

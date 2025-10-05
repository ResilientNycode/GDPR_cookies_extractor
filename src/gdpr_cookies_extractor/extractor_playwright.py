import pandas as pd
import json
import asyncio
import sys
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import ollama
import logging
from datetime import datetime

OLLAMA_MODEL = 'llama3'

logger = logging.getLogger(__name__)

async def handle_cookie_banner(page, action="accept"):
    """
    Finds and clicks the cookie banner button based on the desired action.
    """
    # Define common selectors for "accept" and "reject" buttons.
    # Note: These are common examples, and you might need to add more
    # for different websites in your thesis.
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
            
            if await button.is_visible(timeout=5000):
                logger.info(f"Clicking '{action}' button with selector: {selector}")
                await button.click()
                await page.wait_for_timeout(2000) 
                return True
        except Exception:
            continue
    
    logger.info(f"No '{action}' button found for this site.")
    return False

sync def categorize_cookies_with_llm(cookies_data):
    """
    Categorizes a list of cookies using the LLM.
    Returns a dictionary of categorized cookies.
    """
    # Create the user prompt with the cookies to categorize
    cookies_json_list = json.dumps(cookies_data, indent=2)
    prompt = f"""
    You are an expert in GDPR cookie compliance. Your task is to categorize a list of cookies based on their name and properties.
    Categorize each cookie into one of the following types:
    - "Strictly Necessary": Essential for the website's basic function (e.g., sessions, shopping cart).
    - "Functional": Remembers user choices (e.g., language, preferences).
    - "Analytical": Collects data on user behavior to improve the site (e.g., Google Analytics).
    - "Marketing": Tracks users for advertising and personalization.
    - "Uncategorized": No clear purpose can be determined.
    
    Return a JSON object with the website's cookies categorized into these types.

    Cookies to categorize:
    {cookies_json_list}
    """
    
    try:
        client = ollama.AsyncClient()
        response = await client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {
                    'role': 'system',
                    'content': 'You are a helpful assistant that provides JSON output.'
                },
                {
                    'role': 'user',
                    'content': prompt
                }
            ],
            options={
                'temperature': 0.0
            }
        )
        
        llm_response_content = response['message']['content']
        logger.debug(f"Raw cookie categorization response from LLM: {llm_response_content}")

        # The same robust JSON parsing logic applies here
        start_marker = '```json'
        end_marker = '```'
        if start_marker in llm_response_content:
            start_index = llm_response_content.find(start_marker) + len(start_marker)
            end_index = llm_response_content.find(end_marker, start_index)
            json_string = llm_response_content[start_index:end_index].strip()
        else:
            start_index = llm_response_content.find('{')
            end_index = llm_response_content.rfind('}') + 1
            json_string = llm_response_content[start_index:end_index]
        
        return json.loads(json_string)

    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Error decoding JSON for cookie categorization: {e}")
        logger.debug(f"Raw response: {llm_response_content}")
        return {} # Return an empty dictionary on failure
    except Exception as e:
        logger.error(f"An error occurred during cookie categorization: {e}")
        return {} 

async def call_llm_api(html_content: str, url: str) -> dict:
    """
    Sends HTML content to Ollama to find the privacy policy URL.
    Returns a structured dictionary with the result.
    """
    prompt = f"""
    You are an expert web analysis agent. Your task is to find the URL of the privacy policy page for the given website.
    This page is often linked from the footer, but can also be in a cookie banner, "About Us" section, or other legal notices.
    Analyze the provided HTML content and find the most likely URL for the privacy policy.
    Look for links containing keywords like 'privacy', 'policy', 'GDPR', 'data protection', 'cookie policy', or 'legal notice'.
    The result must be returned as a JSON object.
    
    The HTML content to analyze is below:
    ---
    {html_content}
    ---
    
    The URL of the page is: {url}

    Return your answer as a single JSON object with the following structure:
    {{
      "result_found": <boolean>,
      "privacy_policy_url": <string>,
      "reasoning": <string>,
      "confidence_score": <number>
    }}
    Privacy_policy_url must be the full URL to the privacy page. 
    If no URL is found, set "result_found" to false and "privacy_policy_url" to null.
    Just return the json object, no needs of introduction or other strings in the repsponse. 
    """
    
    try:
        client = ollama.AsyncClient()

        response = await client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {
                    'role': 'system',
                    'content': 'You are a helpful assistant that provides JSON output.'
                },
                {
                    'role': 'user',
                    'content': prompt
                }
            ],
            options={
                'temperature': 0.0
            }
        )

        llm_response_content = response['message']['content']
        logger.debug(f"Raw response from LLM: {llm_response_content}")
        
        try:
            ### this part can be cleaner => do some test modfifing the system role or the promt. 
            start_marker = '```json'
            end_marker = '```'
            if start_marker in llm_response_content:
                start_index = llm_response_content.find(start_marker) + len(start_marker)
                end_index = llm_response_content.find(end_marker, start_index)
                json_string = llm_response_content[start_index:end_index].strip()
            else:
                start_index = llm_response_content.find('{')
                end_index = llm_response_content.rfind('}') + 1
                json_string = llm_response_content[start_index:end_index]
            
            llm_output = json.loads(json_string)
            return llm_output
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Error decoding JSON from LLM response: {e}")
            logger.debug(f"Raw response from LLM: {llm_response_content}")
            return {
                "result_found": False,
                "privacy_policy_url": None,
                "reasoning": "LLM returned malformed JSON.",
                "confidence_score": 0.0
            }
            
    except Exception as e:
        logger.error(f"An error occurred during the Ollama API call: {e}")
        return {
            "result_found": False,
            "privacy_policy_url": None,
            "reasoning": f"Ollama API call failed: {e}",
            "confidence_score": 0.0
        }

def simple_extractor(html_page):
    soup = BeautifulSoup(html_page, "html.parser")

    privacy_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True).lower()
        if "privacy" in href or "privacy" in text:
            privacy_links.append(a["href"])

    privacy_links = list(set(privacy_links))

    logger.info("Privacy related links:")
    for link in privacy_links:
        logger.info(link)

async def main_async(sites_df):
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
                    cookie_categories = await categorize_cookies_with_llm(cookies)
                    logger.info("Cookie categorization complete.")
                    logger.info(f"Categorized Cookies: {cookie_categories}")
                    
                    html_content = await page.content()
                    simple_extractor(html_content)
                    
                    logger.info(" Sending HTML to LLM for analysis...")
                    llm_output = await call_llm_api(html_content, site_url)
                    logger.info("LLM task complete. Processing response.")
                    logger.info(llm_output)
                    
                    await page.close()
                    
                    results.append({
                        "website_url": site_url,
                        "scenario": scenario, # Add the scenario to the result
                        "privacy_policy_url": llm_output.get("privacy_policy_url"),
                        "llm_found": llm_output.get("result_found"),
                        "llm_reasoning": llm_output.get("reasoning"),
                        "cookies_count": len(cookies),
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
                        "cookies_count": 0,
                        "raw_cookies_data": "[]"
                    })
            
        await browser.close()

    results_df = pd.DataFrame(results)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"analysis_results_{timestamp}.csv"
    results_df.to_csv(filename, index=False)
    logger.info("Analysis complete. Results saved to analysis_results.csv")

def main():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"gdpr_analysis_{timestamp}.log"

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler(sys.stdout) 
        ]
    )
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
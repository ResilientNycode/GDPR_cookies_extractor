import pandas as pd
import json
import asyncio
import sys
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import ollama

OLLAMA_MODEL = 'llama3'

async def call_llm_api(html_content: str, url: str) -> dict:
    """
    Sends HTML content to Ollama for privacy policy link extraction.
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
    If no URL is found, set "result_found" to false and "privacy_policy_url" to null.
    """
    
    try:
        # Use the ollama.chat function with the system and user messages
        response = await ollama.chat(
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
                'temperature': 0.0 # Use a low temperature for more deterministic, factual output
            }
        )

        # Ollama's chat response is a dictionary. We extract the message content.
        llm_response_content = response['message']['content']
        
        # The model might include some intro text, so we'll try to find the JSON
        try:
            # Find the JSON object within the text
            start_index = llm_response_content.find('{')
            end_index = llm_response_content.rfind('}') + 1
            json_string = llm_response_content[start_index:end_index]
            
            # Parse the JSON string
            llm_output = json.loads(json_string)
            return llm_output
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  ❌ Error decoding JSON from LLM response: {e}")
            print(f"  Raw response from LLM: {llm_response_content}")
            return {
                "result_found": False,
                "privacy_policy_url": None,
                "reasoning": "LLM returned malformed JSON.",
                "confidence_score": 0.0
            }
            
    except Exception as e:
        print(f"  ❌ An error occurred during the Ollama API call: {e}")
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

    print("Privacy related links:")
    for link in privacy_links:
        print(link)

# Main async function to handle browser automation
async def main_async(sites_df):
    # Load the CSV of sites
    # sites_df = pd.read_csv("sites.csv")
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        for index, row in sites_df.iterrows():
            site_url = row['website_url']
            print(f"Processing: {site_url}")
            
            try:
                page = await browser.new_page()
                
                # 1. Navigate and get cookies
                await page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
                
                # Wait for potential cookie banners or scripts to run
                await page.wait_for_timeout(3000) 

                # Get the cookies
                cookies = await page.context.cookies()
                print(f"  -> Captured {len(cookies)} cookies.")
                
                # 2. Get the HTML content for the LLM
                html_content = await page.content()
                
                # 3. Call the LLM to analyze the content
                simple_extractor(html_content)
                llm_output = await call_llm_api(html_content, site_url)
                
                await page.close()
                
                # 4. Store the results (including cookies)
                results.append({
                    "website_url": site_url,
                    "privacy_policy_url": llm_output.get("privacy_policy_url"),
                    "llm_found": llm_output.get("result_found"),
                    "llm_reasoning": llm_output.get("reasoning"),
                    "cookies_count": len(cookies),
                    "raw_cookies_data": json.dumps(cookies) # Store cookies as a JSON string
                })

            except Exception as e:
                print(f"Error processing {site_url}: {e}")
                results.append({
                    "website_url": site_url,
                    "privacy_policy_url": "N/A",
                    "llm_found": False,
                    "llm_reasoning": f"Failed to process: {e}",
                    "cookies_count": 0,
                    "raw_cookies_data": "[]"
                })
        
        await browser.close()

    # 5. Save the final results to a new CSV
    results_df = pd.DataFrame(results)
    results_df.to_csv("analysis_results.csv", index=False)
    print("Analysis complete. Results saved to analysis_results.csv")


def main():
    print("Running via Poetry script...")

    # Check if a URL was provided on the command line
    if len(sys.argv) < 2:
        print("Error: Please provide a URL as an argument.")
        print("Usage: poetry run main <your_url>")
        sys.exit(1) # Exit because the required argument is missing
        
    # The first argument is the URL (sys.argv[0] is the script name)
    site_url_from_cli = sys.argv[1]
    
    # Create a DataFrame that matches the structure main_async expects
    sites_df = pd.DataFrame([{'website_url': site_url_from_cli}])

    asyncio.run(main_async(sites_df))

if __name__ == "__main__":
    main()
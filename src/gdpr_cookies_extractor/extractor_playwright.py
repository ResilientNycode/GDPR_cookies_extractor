import pandas as pd
import json
import asyncio
import sys
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import ollama

# Set the name of the LLM model you have pulled with Ollama
OLLAMA_MODEL = 'llama3'

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
    privacy_policy_url must be the full URL to the privacy page. 
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
                'temperature': 0.0 # Low temperature for deterministic output
            }
        )

        llm_response_content = response['message']['content']
        print(f"Raw response from LLM: {llm_response_content}")
        
        try:
            # Check for Markdown code block and extract JSON string
            start_marker = '```json'
            end_marker = '```'
            if start_marker in llm_response_content:
                start_index = llm_response_content.find(start_marker) + len(start_marker)
                end_index = llm_response_content.find(end_marker, start_index)
                json_string = llm_response_content[start_index:end_index].strip()
            else:
                # Fallback to finding the first and last braces
                start_index = llm_response_content.find('{')
                end_index = llm_response_content.rfind('}') + 1
                json_string = llm_response_content[start_index:end_index]
            
            # If parsing is successful, return the data
            llm_output = json.loads(json_string)
            return llm_output
            
        except (json.JSONDecodeError, ValueError) as e:
            # Handle cases where the LLM's output is not valid JSON
            print(f"  ❌ Error decoding JSON from LLM response: {e}")
            print(f"  Raw response from LLM: {llm_response_content}")
            return {
                "result_found": False,
                "privacy_policy_url": None,
                "reasoning": "LLM returned malformed JSON.",
                "confidence_score": 0.0
            }
            
    except Exception as e:
        # Handle errors related to the Ollama API call itself
        print(f"  ❌ An error occurred during the Ollama API call: {e}")
        return {
            "result_found": False,
            "privacy_policy_url": None,
            "reasoning": f"Ollama API call failed: {e}",
            "confidence_score": 0.0
        }

def simple_extractor(html_page):
    """
    A simple rule-based function to find privacy-related links using BeautifulSoup.
    This serves as a good baseline for comparison with the LLM's performance.
    """
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

async def main_async(sites_df):
    """
    The main asynchronous function that orchestrates the entire process.
    It loops through sites, uses Playwright to get cookies and HTML, and
    then sends the HTML to the LLM for analysis.
    """
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        for index, row in sites_df.iterrows():
            site_url = row['website_url']
            print(f"Processing: {site_url}")
            
            try:
                page = await browser.new_page()
                
                print("Fetching cookies and HTML...")
                await page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
                
                # Wait for potential cookie banners or scripts to run
                await page.wait_for_timeout(3000) 

                # Get the cookies
                cookies = await page.context.cookies()
                print(f"Captured {len(cookies)} cookies.")
                
                # Get the HTML content for the LLM
                html_content = await page.content()
                
                # Call the simple extractor to show its output
                simple_extractor(html_content)
                
                print("  🧠 Sending HTML to LLM for analysis...")
                llm_output = await call_llm_api(html_content, site_url)
                print("LLM task complete. Processing response.")

                print(llm_output)
                
                await page.close()
                
                # Store the results (including cookies)
                results.append({
                    "website_url": site_url,
                    "privacy_policy_url": llm_output.get("privacy_policy_url"),
                    "llm_found": llm_output.get("result_found"),
                    "llm_reasoning": llm_output.get("reasoning"),
                    "cookies_count": len(cookies),
                    "raw_cookies_data": json.dumps(cookies)
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

    # Save the final results to a new CSV
    results_df = pd.DataFrame(results)
    results_df.to_csv("analysis_results.csv", index=False)
    print("Analysis complete. Results saved to analysis_results.csv")

def main():
    """
    The synchronous entry point for the script.
    It handles command-line arguments and starts the async main loop.
    """
    print("Running via Poetry script...")

    if len(sys.argv) < 2:
        print("Error: Please provide a URL as an argument.")
        print("Usage: poetry run main <your_url>")
        sys.exit(1)
        
    site_url_from_cli = sys.argv[1]
    
    # Create a DataFrame that matches the structure main_async expects
    sites_df = pd.DataFrame([{'website_url': site_url_from_cli}])

    asyncio.run(main_async(sites_df))

if __name__ == "__main__":
    main()
import pandas as pd
import json
import asyncio
# Install Playwright: pip install playwright && playwright install
from playwright.async_api import async_playwright
# Assuming you have the LLM client set up
from your_llm_library import YourLLMClient

# Function to interact with the LLM API (remains the same)
def call_llm_api(html_content, url):
    # ... (your existing LLM API call logic)
    # Placeholder for a real LLM call
    return {
        "result_found": True,
        "privacy_policy_url": f"{url}/privacy-policy-found-by-llm",
        "reasoning": "The LLM found a link with 'privacy' in the footer.",
        "confidence_score": 0.95
    }

# Main async function to handle browser automation
async def main_async():
    # Load the CSV of sites
    sites_df = pd.read_csv("sites.csv")
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
                llm_output = call_llm_api(html_content, site_url)
                
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
                print(f"  ❌ Error processing {site_url}: {e}")
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

if __name__ == "__main__":
    asyncio.run(main_async())
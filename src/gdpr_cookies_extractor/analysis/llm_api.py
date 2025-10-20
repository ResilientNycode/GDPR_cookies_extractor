import json
import ollama
import logging

OLLAMA_MODEL = 'llama3'

logger = logging.getLogger(__name__)


async def analyze_retention_policy(privacy_policy_html):
    """
    Analyzes the HTML of a privacy policy page to find data retention information.
    """
    prompt = f"""
    You are an expert in GDPR compliance. Your task is to find and summarize the data retention policy in the provided privacy policy HTML.
    Look for keywords and phrases related to data retention, such as "data retention", "how long we keep your data", "storage period", or "period for which data is stored".
    Extract and summarize the key information about how long personal data is kept and any conditions for its retention.
    
    If you find the data retention policy, return a JSON object with "retention_found": true and a summary of the policy.
    If you do NOT find a clear retention policy, return a JSON object with "retention_found": false.
    
    Privacy Policy HTML content:
    ---
    {privacy_policy_html}
    ---
    
    Return your answer as a single JSON object with the following structure:
    {{
      "retention_found": <boolean>,
      "retention_policy_summary": <string>,
      "reasoning": <string>
    }}
    If no retention policy is found, set "retention_found" to false and "retention_policy_summary" to null.
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
        logger.error(f"Error decoding JSON for retention analysis: {e}")
        return {"retention_found": False, "retention_policy_summary": None, "reasoning": "LLM returned malformed JSON."}
    except Exception as e:
        logger.error(f"An error occurred during retention analysis: {e}")
        return {"retention_found": False, "retention_policy_summary": None, "reasoning": "Ollama API call failed."}

async def find_dpo(privacy_policy_html, url):
    """
    Analyzes the HTML of a privacy policy page to find DPO contact information.
    If not found, it looks for sub-links to follow.
    """
    # Prompt for DPO extraction on the current page
    dpo_prompt = f"""
    You are an expert in GDPR compliance and a **pure text extractor**. 
    
    ***STRICT RULE: YOU MUST ONLY USE THE TEXT PROVIDED IN THE HTML CONTENT BELOW. DO NOT USE ANY EXTERNAL KNOWLEDGE.***
    
    Your task is to find the official contact details for the Data Protection Officer (DPO).
    
    1.  **STRICT SUCCESS CONDITION**: Only set "dpo_found": true if you find an email address containing 'dpo@' or a clear, dedicated DPO postal address.
    2.  **PRIORITY EXTRACTION**: Search for email addresses containing 'dpo@' first, then 'privacy@', then 'legal@'. Extract the single best email and the main postal address found.
    3.  **SUB-LINK IMPERATIVE**: If the STRICT SUCCESS CONDITION is FALSE, you MUST look for the single most promising **relative URL** sub-link on the page that mentions 'Governance', 'Contact', 'Rights', or 'Data Inquiries'. This link is crucial for finding the DPO.
    
    Privacy Policy HTML content:
    ---
    {privacy_policy_html}
    ---
    
    The URL of the page is: {url}

    Return your answer as a single JSON object with the following structure:
    {{
      "dpo_found": <boolean>,
      "email_address": <string>,  // The best email found (DPO or general privacy contact)
      "postal_address": <string>,
      "sub_link": <string>,       // The most promising relative URL if the DPO email was NOT found.
      "reasoning": <string>
    }}
    If you cannot find ANY email address in the provided HTML text, you MUST set the email_address to null and provide a reasoning that indicates the text was not found.
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
                    'content': dpo_prompt
                }
            ],
            options={
                'temperature': 0.0
            }
        )

        llm_response_content = response['message']['content']
        logger.debug(f"Raw DPO response from LLM: {llm_response_content}")
        
        # Use the same robust JSON parsing logic
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
        logger.error(f"Error decoding JSON for DPO extraction: {e}")
        logger.debug(f"Raw response: {llm_response_content}")
        return {"dpo_found": False, "contact_info": None, "sub_link": None, "reasoning": "LLM returned malformed JSON."}
    except Exception as e:
        logger.error(f"An error occurred during DPO extraction: {e}")
        return {"dpo_found": False, "contact_info": None, "sub_link": None, "reasoning": "Ollama API call failed."}
    


async def find_privacy_policy(html_content, url):
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
    

async def categorize_cookies(cookies_data):
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
from bs4 import BeautifulSoup, Comment

def clean_html_for_text_analysis(html_content: str) -> str:
    """
    Cleans an HTML string by removing scripts, styles, and comments, then extracts the text content.
    This is suitable for LLM analysis where only the main text is needed.
    
    Args:
        html_content: The raw HTML string.
        
    Returns:
        A string containing the cleaned text content of the HTML.
    """
    if not html_content:
        return ""
        
    soup = BeautifulSoup(html_content, 'lxml')
    
    # Remove script and style elements
    for script_or_style in soup(["script", "style"]):
        script_or_style.decompose()
        
    # Remove comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
        
    # Get text from the body, which is now cleaner
    body = soup.find('body')
    if body:
        # Use ' ' as a separator to avoid words sticking together
        text = body.get_text(separator=' ', strip=True)
    else:
        # Fallback if no body tag is found
        text = soup.get_text(separator=' ', strip=True)
        
    # Replace multiple whitespaces with a single space
    cleaned_text = ' '.join(text.split())
    
    return cleaned_text

def clean_html_for_link_extraction(html_content: str) -> str:
    """
    Cleans an HTML string by removing only script and style elements, preserving other tags
    and structure for link extraction.
    
    Args:
        html_content: The raw HTML string.
        
    Returns:
        A string containing the HTML with script and style tags removed.
    """
    if not html_content:
        return ""
        
    soup = BeautifulSoup(html_content, 'lxml')
    
    # Remove script and style elements
    for script_or_style in soup(["script", "style"]):
        script_or_style.decompose()
        
    # Return the modified HTML
    return str(soup)
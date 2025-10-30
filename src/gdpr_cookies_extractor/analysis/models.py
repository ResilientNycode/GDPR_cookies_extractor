import json
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class SiteAnalysisResult:
    # Core Info
    website_url: str
    scenario: str
    
    # High-level results
    privacy_policy_url: Optional[str] = None
    llm_reasoning: Optional[str] = None # Reasoning for the main policy finding
    
    # Cookie Info
    cookies_count: int = 0
    third_party_cookies_count: int = 0
    raw_cookies_data: str = "[]"
    categorized_cookies: str = "{}"
    
    # Extensible dictionary for all sub-analyses
    analyses: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Other collected data
    simple_extractor_links: Optional[List[str]] = None

    @staticmethod
    def from_outputs(
        site_url: str,
        scenario: str,
        cookies: list,
        cookie_categories: dict,
        third_party_count: int,
        llm_output: dict,
        privacy_policy_url: Optional[str] = None,
        simple_extractor_links: Optional[List[str]] = None,
        **analyses: Dict[str, Any]
    ) -> "SiteAnalysisResult":
        
        return SiteAnalysisResult(
            website_url=site_url,
            scenario=scenario,
            privacy_policy_url=privacy_policy_url,
            llm_reasoning=llm_output.get("reasoning"),
            cookies_count=len(cookies),
            third_party_cookies_count=third_party_count,
            raw_cookies_data=json.dumps(cookies),
            categorized_cookies=json.dumps(cookie_categories),
            simple_extractor_links=simple_extractor_links,
            analyses=analyses
        )

    @staticmethod
    def from_exception(
        site_url: str,
        scenario: str,
        e: Exception
    ) -> "SiteAnalysisResult":
        return SiteAnalysisResult(
            website_url=site_url,
            scenario=scenario,
            llm_reasoning=f"Failed to process: {e}",
        )

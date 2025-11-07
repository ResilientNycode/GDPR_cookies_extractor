from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class SiteAnalysisResult:
    """
    A dataclass to hold the complete analysis results for a single site and scenario.
    """
    # Core Info
    website_url: str
    scenario: str
    
    # Cookie Info
    cookies_count: int = 0
    third_party_cookies_count: int = 0
    raw_cookies_data: List[Dict[str, Any]] = field(default_factory=list)
    categorized_cookies: List[Dict[str, Any]] = field(default_factory=list)
    
    # Dictionary for all analysis results (privacy policy, cookies, deletion, etc.)
    analyses: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Other collected data
    simple_extractor_links: Optional[List[str]] = None
    error_message: Optional[str] = None

    @staticmethod
    def from_exception(
        site_url: str,
        scenario: str,
        e: Exception
    ) -> "SiteAnalysisResult":
        """Creates a result object from an exception."""
        return SiteAnalysisResult(
            website_url=site_url,
            scenario=scenario,
            error_message=f"Failed to process: {e}",
        )
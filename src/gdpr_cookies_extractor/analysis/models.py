import json
from dataclasses import dataclass, asdict
from typing import Optional, List

@dataclass
class SiteAnalysisResult:
    website_url: str
    scenario: str
    privacy_policy_url: Optional[str] = None
    llm_reasoning: Optional[str] = None
    dpo_email: Optional[str] = None
    dpo_address: Optional[str] = None
    dpo_reasoning: Optional[str] = None
    dpo_url: Optional[str] = None
    retention_policy_summary: Optional[str] = None
    retention_reasoning: Optional[str] = None
    retention_policy_url: Optional[str] = None
    cookies_count: int = 0
    third_party_cookies_count: int = 0
    raw_cookies_data: str = "[]"
    categorized_cookies: str = "{}"
    simple_extractor_links: Optional[List[str]] = None

    @staticmethod
    def from_outputs(
        site_url: str,
        scenario: str,
        cookies: list,
        cookie_categories: dict,
        third_party_count: int,
        llm_output: dict,
        dpo_output: dict,
        retention_output: dict,
        privacy_policy_url: Optional[str] = None,
        simple_extractor_links: Optional[List[str]] = None
    ) -> "SiteAnalysisResult":
        return SiteAnalysisResult(
            website_url=site_url,
            scenario=scenario,
            privacy_policy_url=privacy_policy_url,
            llm_reasoning=llm_output.get("reasoning"),
            dpo_email=dpo_output.get("email_address"),
            dpo_address=dpo_output.get("postal_address"),
            dpo_reasoning=dpo_output.get("reasoning"),
            dpo_url=dpo_output.get("source_url"),
            retention_policy_summary=retention_output.get("retention_policy_summary"),
            retention_reasoning=retention_output.get("reasoning"),
            retention_policy_url=retention_output.get("source_url"),
            cookies_count=len(cookies),
            third_party_cookies_count=third_party_count,
            raw_cookies_data=json.dumps(cookies),
            categorized_cookies=json.dumps(cookie_categories),
            simple_extractor_links=simple_extractor_links
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
            dpo_reasoning=f"Failed to process: {e}",
            retention_reasoning=f"Failed to process: {e}"
        )

"""
Crawl4AI integration (Playwright-based, MIT license). [Z1-3]

Domain-locked, 2-tier-depth crawl of probed portals; retrieves candidate
document URLs (PDF/HTML). Chosen over Scrapy/custom crawler for JS-rendering
support — see RDTII_Engine_Technical_Plan_v2.docx §6 Technology Stack.

TODO: async def crawl(portal_url, depth=2) -> list[CandidateDocument]
"""

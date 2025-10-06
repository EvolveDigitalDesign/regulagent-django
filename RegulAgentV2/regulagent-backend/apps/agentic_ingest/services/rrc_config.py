from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RRCSelectors:
    username_input: str = "#username"
    password_input: str = "#password"
    login_button: str = "button[type=submit]"
    api_search_input: str = "input[name=\"searchArgs.apiNoHndlr.inputValue\"]"
    search_button: str = "input[type='button'][value='Search'][onclick='doSearch();']"
    # On detail page we'll specifically look for these href patterns
    result_links_selector: str = "a[href*='viewPdfReportFormAction.do'], a[href*='dpimages/r/']"


@dataclass
class RRCConfig:
    login_url: str = ""
    search_url: str = ""
    selectors: RRCSelectors = field(default_factory=RRCSelectors)


DEFAULT_RRC_CONFIG = RRCConfig(
    login_url="",  # Not required for public completions
    search_url=(
        "https://webapps.rrc.texas.gov/CMPL/publicSearchAction.do?"
        "formData.methodHndlr.inputValue=init&formData.headerTabSelected=home&"
        "formData.pageForwardHndlr.inputValue=home"
    ),
)



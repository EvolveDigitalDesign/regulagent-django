import logging
import re

from .jurisdiction_handler import JurisdictionHandler

logger = logging.getLogger(__name__)

_HANDLERS: dict[str, JurisdictionHandler] = {}

_API_PREFIX_MAP: dict[str, str] = {
    "42": "TX",
    "30": "NM",
}


def register_handler(handler: JurisdictionHandler) -> None:
    code = handler.jurisdiction_code.upper()
    if code in _HANDLERS:
        raise ValueError(f"Handler for jurisdiction '{code}' is already registered.")
    _HANDLERS[code] = handler
    logger.info("Registered jurisdiction handler: %s", code)


def get_handler(jurisdiction_code: str) -> JurisdictionHandler | None:
    return _HANDLERS.get(jurisdiction_code.upper())


def get_handler_by_policy_id(policy_id: str) -> JurisdictionHandler | None:
    for handler in _HANDLERS.values():
        if handler.policy_pack_config.policy_id == policy_id:
            return handler
    return None


def get_handler_by_api_prefix(api_number: str) -> JurisdictionHandler | None:
    prefix = api_number[:2]
    jurisdiction_code = _API_PREFIX_MAP.get(prefix)
    if jurisdiction_code is None:
        return None
    return _HANDLERS.get(jurisdiction_code)


def list_handlers() -> dict[str, JurisdictionHandler]:
    return dict(_HANDLERS)


def clear_registry() -> None:
    """For testing only — clears all registered handlers."""
    _HANDLERS.clear()


def detect_jurisdiction(api_number: str, explicit: str | None = None) -> str:
    """Return a jurisdiction code (e.g. 'TX', 'NM') from an API number or explicit override.

    Strips non-digit characters before checking the first 2 digits against
    _API_PREFIX_MAP, so inputs like '30-015-28692' and '3001528692' both resolve
    to 'NM'.  Falls back to 'TX' if the prefix is unknown.
    """
    if explicit:
        return explicit.upper()
    normalized = re.sub(r"\D+", "", str(api_number or ""))
    prefix = normalized[:2]
    return _API_PREFIX_MAP.get(prefix, "TX")

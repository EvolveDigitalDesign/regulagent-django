from typing import Callable, Dict

from .policy_kernel import plan_from_facts


_REGISTRY: Dict[str, Callable] = {
    # Default W-3A handler maps to plan_from_facts (stub today)
    'tx.w3a': plan_from_facts,
}


def get_policy_handler(policy_id: str) -> Callable:
    return _REGISTRY.get(policy_id, plan_from_facts)


def register_policy_handler(policy_id: str, handler: Callable) -> None:
    _REGISTRY[policy_id] = handler



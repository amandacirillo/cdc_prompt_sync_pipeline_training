"""Combinatorial expansion: one guideline_group per tier, cross-multiplied
across tiers, produces the complete set of prompt variants for a scenario.

Each combination's prompt text doesn't inline guideline text -- it
references each guideline's own already-synced prompt by name (a
``@@@promptRef:name=...|label=production@@@`` marker, mirroring Langfuse's
prompt-composition syntax). That keeps a single guideline's text as the one
source of truth: editing it never requires re-syncing every combination
that happens to include it.
"""
from itertools import product
from typing import Any, Dict, List, Tuple

GUIDELINE_PROMPT_PREFIX = "guideline"
COMBO_PROMPT_PREFIX = "combo"


def guideline_prompt_name(scenario_id: int, guideline: Dict[str, Any]) -> str:
    return f"scenario_{scenario_id}/{GUIDELINE_PROMPT_PREFIX}/{guideline['type']}_{guideline['id']}"


def build_guideline_prompt_body(guideline: Dict[str, Any]) -> str:
    """A ``text`` guideline's prompt body is its raw text. A ``toggle``
    guideline's prompt body wraps it in a Jinja-style conditional keyed on
    its own variable name, so a combination prompt can turn it on/off."""
    text = guideline.get("guideline_text", "")
    if guideline.get("type") == "toggle":
        variable = f"toggle_{guideline['id']}"
        return f"{{% if {variable} %}}{text}{{% endif %}}"
    return text


def generate_combinations(
    scenario: Dict[str, Any],
    guideline_groups: List[Dict[str, Any]],
    guidelines: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return ``(combinations, unique_guidelines)``.

    ``combinations`` has one entry per cartesian-product combination of
    guideline_groups across tiers. ``unique_guidelines`` is every guideline
    referenced by at least one combination, deduplicated -- the caller uses
    this list to sync each guideline's own prompt before syncing the
    combinations that reference it.
    """
    groups_by_tier: Dict[int, List[Dict[str, Any]]] = {}
    for group in guideline_groups:
        groups_by_tier.setdefault(group["tier"], []).append(group)

    if not groups_by_tier:
        return [], []

    guidelines_by_group: Dict[int, List[Dict[str, Any]]] = {}
    for guideline in guidelines:
        guidelines_by_group.setdefault(guideline["guideline_group_id"], []).append(guideline)

    sorted_tiers = sorted(groups_by_tier.keys())
    tier_group_lists = [groups_by_tier[tier] for tier in sorted_tiers]

    scenario_id = scenario["id"]
    queue_id = scenario["queue_id"]

    combinations: List[Dict[str, Any]] = []
    unique_guidelines: Dict[int, Dict[str, Any]] = {}

    for group_combo in product(*tier_group_lists):
        combo_guideline_ids: List[int] = []
        name_parts = [f"queue_{queue_id}/scenario_{scenario_id}/{COMBO_PROMPT_PREFIX}"]
        prompt_refs: List[str] = []

        combo_guidelines = []
        for group in group_combo:
            name_parts.append(f"t{group['tier']}_{group['id']}")
            for guideline in sorted(
                guidelines_by_group.get(group["id"], []), key=lambda g: g.get("priority", 0)
            ):
                unique_guidelines[guideline["id"]] = guideline
                combo_guideline_ids.append(guideline["id"])
                combo_guidelines.append(guideline)

        for guideline in combo_guidelines:
            prompt_name = guideline_prompt_name(scenario_id, guideline)
            prompt_refs.append(f"@@@promptRef:name={prompt_name}|label=production@@@")

        combinations.append(
            {
                "scenario_id": scenario_id,
                "queue_id": queue_id,
                "guideline_group_ids": [g["id"] for g in group_combo],
                "guideline_ids": combo_guideline_ids,
                "combination_name": "_".join(name_parts),
                "prompt_text": "\n".join(prompt_refs),
            }
        )

    return combinations, list(unique_guidelines.values())

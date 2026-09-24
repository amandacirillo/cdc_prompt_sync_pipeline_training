from typing import Any, Dict, List, Tuple

from combinations import build_guideline_prompt_body, generate_combinations, guideline_prompt_name
from fakes import default_tables


def _scenario_fixture() -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    tables = default_tables()
    scenario = tables["scenario"][0]
    groups = tables["guideline_group"]
    guidelines = tables["guideline"]
    return scenario, groups, guidelines


def test_generate_combinations_cartesian_product_across_tiers() -> None:
    scenario, groups, guidelines = _scenario_fixture()
    combos, unique_guidelines = generate_combinations(scenario, groups, guidelines)

    # 2 tier-1 groups x 2 tier-2 groups = 4 combinations
    assert len(combos) == 4
    assert {tuple(sorted(c["guideline_group_ids"])) for c in combos} == {
        (1, 3), (1, 4), (2, 3), (2, 4),
    }


def test_generate_combinations_unique_guidelines_deduped() -> None:
    scenario, groups, guidelines = _scenario_fixture()
    _, unique_guidelines = generate_combinations(scenario, groups, guidelines)

    # Every guideline appears in exactly one combo per its group, but should
    # only be counted once overall.
    assert len(unique_guidelines) == len(guidelines)
    assert {g["id"] for g in unique_guidelines} == {1, 2, 3, 4, 5}


def test_generate_combinations_prompt_text_references_every_guideline() -> None:
    scenario, groups, guidelines = _scenario_fixture()
    combos, _ = generate_combinations(scenario, groups, guidelines)

    combo_1_3 = next(c for c in combos if sorted(c["guideline_group_ids"]) == [1, 3])
    assert "@@@promptRef:name=scenario_1/guideline/text_1|label=production@@@" in combo_1_3["prompt_text"]
    assert "@@@promptRef:name=scenario_1/guideline/text_3|label=production@@@" in combo_1_3["prompt_text"]
    assert "@@@promptRef:name=scenario_1/guideline/toggle_4|label=production@@@" in combo_1_3["prompt_text"]


def test_generate_combinations_empty_groups_returns_empty() -> None:
    scenario, _, guidelines = _scenario_fixture()
    combos, unique_guidelines = generate_combinations(scenario, [], guidelines)
    assert combos == []
    assert unique_guidelines == []


def test_guideline_prompt_name() -> None:
    assert guideline_prompt_name(1, {"id": 4, "type": "toggle"}) == "scenario_1/guideline/toggle_4"


def test_build_guideline_prompt_body_text_is_verbatim() -> None:
    guideline = {"type": "text", "guideline_text": "Be warm."}
    assert build_guideline_prompt_body(guideline) == "Be warm."


def test_build_guideline_prompt_body_toggle_wraps_conditional() -> None:
    guideline = {"id": 4, "type": "toggle", "guideline_text": "Offer credit."}
    assert build_guideline_prompt_body(guideline) == "{% if toggle_4 %}Offer credit.{% endif %}"

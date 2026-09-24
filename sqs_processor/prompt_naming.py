"""Guideline prompt naming/body helpers.

Deliberately duplicated (not imported) from `dynamodb_handler/combinations.py`
-- each Lambda's deployment package here is self-contained (mirroring the
real system's structure, where the shared Lambda layer carries only
third-party dependencies, never domain code). Keeping the two tiny
functions below in sync is a one-line diff; it's a fair trade for not
having to version and deploy a second internal layer just for this.
"""
from typing import Any, Dict


def guideline_prompt_name(scenario_id: int, guideline: Dict[str, Any]) -> str:
    return f"scenario_{scenario_id}/guideline/{guideline['type']}_{guideline['id']}"


def build_guideline_prompt_body(guideline: Dict[str, Any]) -> str:
    text = guideline.get("guideline_text", "")
    if guideline.get("type") == "toggle":
        variable = f"toggle_{guideline['id']}"
        return f"{{% if {variable} %}}{text}{{% endif %}}"
    return text

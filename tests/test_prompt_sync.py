import time

import pytest
from fakes import FakePrompt, FakePromptStore
from prompt_sync import run_with_timeout, sanitize_label, sync_combinations, sync_guidelines


def test_run_with_timeout_returns_result() -> None:
    assert run_with_timeout(lambda x: x + 1, 1, 41) == 42


def test_run_with_timeout_raises_on_slow_call() -> None:
    def slow() -> None:
        time.sleep(0.3)

    with pytest.raises(TimeoutError):
        run_with_timeout(slow, 0.05)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", "unknown"),
        ("Hello World", "hello_world"),
        ("!!!weird***", "weird"),
        ("a" * 50, "a" * 36),
    ],
)
def test_sanitize_label(text: str, expected: str) -> None:
    assert sanitize_label(text) == expected


def test_sync_guidelines_creates_new_prompts() -> None:
    store = FakePromptStore()
    guidelines = [
        {"id": 1, "type": "text", "guideline_text": "Be warm.", "priority": 10},
        {"id": 4, "type": "toggle", "guideline_text": "Offer credit.", "priority": 30},
    ]

    successful, failed = sync_guidelines(store, scenario_id=1, guidelines=guidelines)

    assert failed == []
    assert successful == ["scenario_1/guideline/text_1", "scenario_1/guideline/toggle_4"]
    assert store.prompts["scenario_1/guideline/toggle_4"].prompt == "{% if toggle_4 %}Offer credit.{% endif %}"


def test_sync_guidelines_deprecate_tags_existing_prompt_only() -> None:
    store = FakePromptStore()
    guideline = {"id": 1, "type": "text", "guideline_text": "Be warm.", "priority": 10}
    sync_guidelines(store, scenario_id=1, guidelines=[guideline])  # create it first

    successful, failed = sync_guidelines(store, scenario_id=1, guidelines=[guideline], deprecated=True)
    assert successful == ["scenario_1/guideline/text_1"]
    assert "deprecated" in store.prompts["scenario_1/guideline/text_1"].tags


def test_sync_guidelines_deprecate_skips_nonexistent_prompt() -> None:
    store = FakePromptStore()
    guideline = {"id": 99, "type": "text", "guideline_text": "never synced", "priority": 0}
    successful, failed = sync_guidelines(store, scenario_id=1, guidelines=[guideline], deprecated=True)
    assert successful == []
    assert failed == []


def test_sync_combinations_creates_and_reports_failures() -> None:
    class FlakyStore(FakePromptStore):
        def create_prompt(self, **kwargs: object) -> "FakePrompt":  # type: ignore[override]
            if kwargs["name"] == "bad-combo":
                raise RuntimeError("boom")
            return super().create_prompt(**kwargs)  # type: ignore[arg-type]

    store = FlakyStore()
    combos = [
        {
            "combination_name": "good-combo",
            "prompt_text": "text",
            "scenario_id": 1,
            "queue_id": 1,
            "guideline_group_ids": [1, 3],
            "guideline_ids": [1, 3],
        },
        {
            "combination_name": "bad-combo",
            "prompt_text": "text",
            "scenario_id": 1,
            "queue_id": 1,
            "guideline_group_ids": [1, 4],
            "guideline_ids": [1, 5],
        },
    ]

    successful, failed = sync_combinations(store, combos)
    assert successful == ["good-combo"]
    assert failed == ["bad-combo"]

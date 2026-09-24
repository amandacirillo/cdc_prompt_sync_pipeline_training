import json

from fakes import FakePromptStore
from sqs_processor import _handle, process_message


def _sqs_record(message: dict, message_id: str = "m1") -> dict:
    return {"messageId": message_id, "body": json.dumps(message)}


def test_process_message_sync_guidelines() -> None:
    store = FakePromptStore()
    message = {
        "event_type": "SYNC_GUIDELINES",
        "operation": "update",
        "scenario_id": 1,
        "guidelines": [{"id": 1, "type": "text", "guideline_text": "Be warm.", "priority": 10}],
    }
    result = process_message(message, store)
    assert result["event_type"] == "SYNC_GUIDELINES"
    assert result["successful"] == ["scenario_1/guideline/text_1"]


def test_process_message_sync_combinations() -> None:
    store = FakePromptStore()
    message = {
        "event_type": "SYNC_COMBINATIONS",
        "operation": "update",
        "combinations": [
            {
                "combination_name": "combo-1",
                "prompt_text": "text",
                "scenario_id": 1,
                "queue_id": 1,
                "guideline_group_ids": [1, 3],
                "guideline_ids": [1, 3],
            }
        ],
    }
    result = process_message(message, store)
    assert result["successful"] == ["combo-1"]


def test_process_message_deprecate() -> None:
    store = FakePromptStore()
    store.create_prompt(name="scenario_1/guideline/text_1", prompt="Be warm.", type="text", labels=[])

    message = {"event_type": "DEPRECATE", "scenario_id": 1, "item_id": 1, "item_type": "text"}
    result = process_message(message, store)
    assert result["prompt_name"] == "scenario_1/guideline/text_1"
    assert result["successful"] == ["scenario_1/guideline/text_1"]
    assert "deprecated" in store.prompts["scenario_1/guideline/text_1"].tags


def test_handle_aggregates_results_and_flushes() -> None:
    store = FakePromptStore()
    event = {
        "Records": [
            _sqs_record(
                {
                    "event_type": "SYNC_GUIDELINES",
                    "operation": "update",
                    "scenario_id": 1,
                    "guidelines": [{"id": 1, "type": "text", "guideline_text": "Be warm.", "priority": 10}],
                },
                message_id="m1",
            ),
            {"messageId": "bad", "body": "not json"},
        ]
    }

    result = _handle(event, store)

    assert result["statusCode"] == 207
    assert result["processed_count"] == 1
    assert result["failed_count"] == 1
    assert result["failures"][0]["messageId"] == "bad"
    assert store.flushed is True

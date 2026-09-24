import json

from dynamodb_processor import _handle, _promote_to_recompute_scope, process_remove_record
from fakes import FakeMariaDBConnection, FakeSqsClient

QUEUE_URL = "http://sqs:9324/queue/support-processing-queue"


def _remove_record(deleted_item: dict, event_id: str = "e1") -> dict:
    return {
        "eventID": event_id,
        "eventName": "REMOVE",
        "dynamodb": {"OldImage": {"data": deleted_item}},
    }


def test_promote_guideline_to_owning_group() -> None:
    table, item_id = _promote_to_recompute_scope(
        "guideline", 4, {"guideline_group_id": 3, "tableName": "guideline", "id": 4}
    )
    assert (table, item_id) == ("guideline_group", 3)


def test_promote_guideline_group_to_owning_scenario() -> None:
    table, item_id = _promote_to_recompute_scope(
        "guideline_group", 3, {"scenario_id": 1, "tableName": "guideline_group", "id": 3}
    )
    assert (table, item_id) == ("scenario", 1)


def test_promote_scenario_stays_as_is() -> None:
    table, item_id = _promote_to_recompute_scope("scenario", 1, {"tableName": "scenario", "id": 1})
    assert (table, item_id) == ("scenario", 1)


def test_process_remove_record_update_sends_staged_sync_messages() -> None:
    conn = FakeMariaDBConnection()
    sqs = FakeSqsClient()
    deleted_item = {"id": 3, "tableName": "guideline_group", "operation": "update", "scenario_id": 1}

    result = process_remove_record(_remove_record(deleted_item), conn, sqs, QUEUE_URL)

    assert result["messagesSent"] == 2
    bodies = [json.loads(m["MessageBody"]) for m in sqs.sent_messages]
    event_types = [b["event_type"] for b in bodies]
    assert event_types == ["SYNC_GUIDELINES", "SYNC_COMBINATIONS"]

    guidelines_msg = bodies[0]
    assert len(guidelines_msg["guidelines"]) == 5
    combos_msg = bodies[1]
    assert len(combos_msg["combinations"]) == 4
    # The combinations message is delayed so it lands after guidelines sync.
    assert sqs.sent_messages[1]["DelaySeconds"] > sqs.sent_messages[0]["DelaySeconds"]


def test_process_remove_record_hard_delete_sends_deprecate_message() -> None:
    conn = FakeMariaDBConnection()
    sqs = FakeSqsClient()
    deleted_item = {"id": 4, "tableName": "guideline", "operation": "delete", "guideline_group_id": 3, "type": "toggle"}

    result = process_remove_record(_remove_record(deleted_item), conn, sqs, QUEUE_URL)

    assert result["messagesSent"] == 3
    bodies = [json.loads(m["MessageBody"]) for m in sqs.sent_messages]
    event_types = [b["event_type"] for b in bodies]
    assert event_types == ["SYNC_GUIDELINES", "SYNC_COMBINATIONS", "DEPRECATE"]
    deprecate_msg = bodies[-1]
    assert deprecate_msg["table_name"] == "guideline"
    assert deprecate_msg["item_id"] == 4


def test_handle_skips_non_remove_events_and_reports_failures() -> None:
    conn = FakeMariaDBConnection()
    sqs = FakeSqsClient()
    event = {
        "Records": [
            {"eventID": "skip-me", "eventName": "INSERT"},
            _remove_record({"id": 1, "tableName": "scenario", "operation": "update"}, event_id="ok"),
            _remove_record({"tableName": "guideline"}, event_id="broken"),  # missing id
        ]
    }

    result = _handle(event, conn, sqs, QUEUE_URL)

    assert result["statusCode"] == 207
    assert len(result["processed"]) == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["eventID"] == "broken"

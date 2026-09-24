"""DynamoDB-Streams-triggered dispatcher (filtered to REMOVE events only --
see template.yaml FilterCriteria).

DynamoDB is used here purely as a durable, ordered *trigger bus*: producers
write a short-TTL item carrying the changed row's data; nothing ever reads
that item back. What matters is its eventual TTL-expiry delete, which
DynamoDB Streams reports as a REMOVE event with the deleted item still
attached as `OldImage`. That gives every downstream consumer a durable,
replayable "a change happened" signal without a producer needing to know
who's listening.

On each REMOVE event, this handler:
  1. Promotes the changed item's id up to the nearest ancestor that still
     needs its combinations recomputed (a `guideline` change is re-resolved
     as its owning `guideline_group`; a `guideline_group` change as its
     owning `scenario`) -- see `_promote_to_recompute_scope`.
  2. Re-reads only the MariaDB rows needed to regenerate every prompt
     combination touched by that change (db_utils.fetch_related_data).
  3. Computes those combinations (combinations.generate_combinations) and
     fans the sync work out to SQS as two staged messages per scenario: an
     immediate "sync every referenced guideline's own prompt" message, and
     a delayed "sync every combination prompt" message -- the delay gives
     the guideline-level sync a head start, since every combination prompt
     *references* its guidelines' prompts by name and shouldn't be synced
     before they exist.
"""
import json
import logging
import os
from typing import Any, Dict, List
from urllib.parse import urlparse

import boto3
import pymysql

from combinations import generate_combinations
from db_utils import fetch_related_data

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

if os.environ.get("DEBUG_MODE") == "true":
    import debugpy

    debug_port = int(os.environ.get("DEBUG_PORT", "5890"))
    debugpy.listen(("0.0.0.0", debug_port))
    debugpy.wait_for_client()
    logger.info("Debugger attached on port %s", debug_port)

# Items promoted to a scope requiring a recompute (rather than looked up
# as-is), keyed by the table the changed item lives in.
_PROMOTE_TO_PARENT = {
    "guideline": "guideline_group_id",
    "guideline_group": "scenario_id",
}


def get_sqs_client() -> Any:
    endpoint = os.environ.get("SQS_ENDPOINT")
    kwargs: Dict[str, Any] = {"region_name": "us-east-1"}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
        kwargs["aws_access_key_id"] = os.environ.get("AWS_ACCESS_KEY_ID", "fake")
        kwargs["aws_secret_access_key"] = os.environ.get("AWS_SECRET_ACCESS_KEY", "fake")
    return boto3.client("sqs", **kwargs)


def get_mariadb_connection() -> Any:
    database_uri = os.environ.get("DATABASE_URI")
    if database_uri:
        parsed = urlparse(database_uri)
        host, port = parsed.hostname, parsed.port or 3306
        user, password = parsed.username, parsed.password or ""
        database = parsed.path.lstrip("/")
    else:
        host = os.environ.get("MARIADB_HOST", "localhost")
        port = int(os.environ.get("MARIADB_PORT", "3306"))
        user = os.environ.get("MARIADB_USER", "root")
        password = os.environ.get("MARIADB_PASSWORD", "")
        database = os.environ.get("MARIADB_DATABASE", "testdb")

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=30,
    )


def _promote_to_recompute_scope(table_name: str, item_id: int, deleted_item: Dict[str, Any]) -> tuple:
    """A change to a leaf (`guideline`) or mid-tier (`guideline_group`) row
    doesn't have combinations of its own -- promote to the nearest ancestor
    whose combinations need recomputing. Returns ``(table_name, item_id)``.
    """
    parent_field = _PROMOTE_TO_PARENT.get(table_name)
    if parent_field and deleted_item.get(parent_field) is not None:
        parent_table = "guideline_group" if table_name == "guideline" else "scenario"
        return parent_table, int(deleted_item[parent_field])
    return table_name, item_id


def process_remove_record(record: Dict[str, Any], mariadb_conn: Any, sqs_client: Any, queue_url: str) -> Dict[str, Any]:
    old_image = record.get("dynamodb", {}).get("OldImage", {})
    deleted_item = old_image.get("data", {})

    table_name = deleted_item.get("tableName")
    item_id = deleted_item.get("id")
    operation = deleted_item.get("operation", "update")

    if not table_name or item_id is None:
        raise ValueError(f"Malformed trigger payload, missing tableName/id: {deleted_item}")

    recompute_table, recompute_id = _promote_to_recompute_scope(table_name, int(item_id), deleted_item)

    related_data = fetch_related_data(recompute_id, recompute_table, mariadb_conn)
    scenarios = related_data.get("scenarios", [])
    all_groups = related_data.get("guideline_groups", [])
    all_guidelines = related_data.get("guidelines", [])

    messages_sent = 0
    for scenario in scenarios:
        scenario_groups = [g for g in all_groups if g["scenario_id"] == scenario["id"]]
        group_ids = {g["id"] for g in scenario_groups}
        scenario_guidelines = [g for g in all_guidelines if g["guideline_group_id"] in group_ids]

        combos, unique_guidelines = generate_combinations(scenario, scenario_groups, scenario_guidelines)
        if not unique_guidelines and not combos:
            continue

        guideline_delay = max(5, len(unique_guidelines) // 5)

        send_sync_message(
            sqs_client,
            queue_url,
            {
                "event_type": "SYNC_GUIDELINES",
                "operation": operation,
                "scenario_id": scenario["id"],
                "guidelines": unique_guidelines,
            },
            delay_seconds=0,
        )
        messages_sent += 1

        send_sync_message(
            sqs_client,
            queue_url,
            {
                "event_type": "SYNC_COMBINATIONS",
                "operation": operation,
                "scenario_id": scenario["id"],
                "combinations": combos,
            },
            delay_seconds=guideline_delay,
        )
        messages_sent += 1

    # A hard delete (not an update) of a leaf/mid-tier row means its own
    # prompt -- and any now-orphaned combinations -- should be deprecated,
    # not just left stale. We tag it explicitly instead of relying on the
    # recompute above (which only re-syncs *surviving* rows).
    if operation == "delete" and table_name != recompute_table:
        send_sync_message(
            sqs_client,
            queue_url,
            {
                "event_type": "DEPRECATE",
                "table_name": table_name,
                "item_id": item_id,
                "item_type": deleted_item.get("type"),
            },
            delay_seconds=0,
        )
        messages_sent += 1

    return {"tableName": table_name, "itemId": item_id, "operation": operation, "messagesSent": messages_sent}


def send_sync_message(sqs_client: Any, queue_url: str, message: Dict[str, Any], delay_seconds: int) -> None:
    sqs_client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message, default=str),
        DelaySeconds=delay_seconds,
        MessageAttributes={"EventType": {"StringValue": message["event_type"], "DataType": "String"}},
    )


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    mariadb_conn = get_mariadb_connection()
    sqs_client = get_sqs_client()
    queue_url = os.environ.get("SQS_QUEUE_URL", "http://localhost:9324/queue/support-processing-queue")
    return _handle(event, mariadb_conn, sqs_client, queue_url)


def _handle(event: Dict[str, Any], mariadb_conn: Any, sqs_client: Any, queue_url: str) -> Dict[str, Any]:
    processed: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for record in event.get("Records", []):
        if record.get("eventName") != "REMOVE":
            logger.info("Skipping non-REMOVE event: %s", record.get("eventName"))
            continue
        try:
            processed.append(process_remove_record(record, mariadb_conn, sqs_client, queue_url))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to process record %s: %s", record.get("eventID"), exc)
            failed.append({"eventID": record.get("eventID"), "error": str(exc)})

    return {
        "statusCode": 200 if not failed else 207,
        "processed": processed,
        "failed": failed,
    }

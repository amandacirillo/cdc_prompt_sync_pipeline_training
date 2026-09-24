"""SQS-triggered processor: consumes staged sync messages produced by the
DynamoDB stream handler and syncs the corresponding prompts to Langfuse.

Handles three message shapes (see dynamodb_handler/dynamodb_processor.py):
  - SYNC_GUIDELINES: sync each guideline's own prompt (sent with no delay)
  - SYNC_COMBINATIONS: sync each computed combination's prompt (sent with a
    delay, so it lands after the guidelines it references have synced)
  - DEPRECATE: tag a single guideline's own prompt as deprecated, for a
    hard delete that isn't part of a larger recompute
"""
import json
import logging
import os
import sys
from typing import Any, Dict

from langfuse import Langfuse

from prompt_naming import guideline_prompt_name
from prompt_sync import sync_combinations, sync_guidelines

if os.environ.get("DEBUG_MODE") == "true":
    import debugpy

    debug_port = int(os.environ.get("DEBUG_PORT", "5891"))
    debugpy.listen(("0.0.0.0", debug_port))
    debugpy.wait_for_client()

logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True)
logger = logging.getLogger(__name__)

_langfuse_client: Any = None


def get_langfuse_client() -> Langfuse:
    global _langfuse_client
    if _langfuse_client is None:
        _langfuse_client = Langfuse(
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
            host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            timeout=30,
        )
    return _langfuse_client


def process_message(message: Dict[str, Any], prompt_store: Any) -> Dict[str, Any]:
    event_type = message.get("event_type")
    deprecated = message.get("operation") == "delete"

    if event_type == "SYNC_GUIDELINES":
        successful, failed = sync_guidelines(
            prompt_store, message["scenario_id"], message.get("guidelines", []), deprecated=False
        )
        return {"event_type": event_type, "successful": successful, "failed": failed}

    if event_type == "SYNC_COMBINATIONS":
        successful, failed = sync_combinations(
            prompt_store, message.get("combinations", []), deprecated=deprecated
        )
        return {"event_type": event_type, "successful": successful, "failed": failed}

    if event_type == "DEPRECATE":
        item = {"id": message["item_id"], "type": message.get("item_type", "text")}
        prompt_name = guideline_prompt_name(message.get("scenario_id", 0), item)
        successful, failed = sync_guidelines(
            prompt_store, message.get("scenario_id", 0), [item], deprecated=True
        )
        return {"event_type": event_type, "prompt_name": prompt_name, "successful": successful, "failed": failed}

    raise ValueError(f"Unknown event_type: {event_type}")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return _handle(event, get_langfuse_client())


def _handle(event: Dict[str, Any], prompt_store: Any) -> Dict[str, Any]:
    results = []
    failures = []

    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            result = process_message(message, prompt_store)
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to process SQS record %s: %s", record.get("messageId"), exc)
            failures.append({"messageId": record.get("messageId"), "error": str(exc)})

    try:
        prompt_store.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to flush prompt store client: %s", exc)

    return {
        "statusCode": 200 if not failures else 207,
        "processed_count": len(results),
        "failed_count": len(failures),
        "results": results,
        "failures": failures,
    }

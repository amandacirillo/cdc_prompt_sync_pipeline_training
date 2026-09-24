"""Sync guideline and combination prompts to an external prompt-management
service (Langfuse in this training; the pattern generalizes to any
create/get "prompt" API).

Each individual Langfuse SDK call is wrapped in `run_with_timeout` --
a `ThreadPoolExecutor`-based per-call timeout -- so one slow or hung network
call can't stall an entire batch of guideline/combination syncs. This
mirrors the real system's approach; the alternative (a single timeout for
the whole batch) means one bad call wastes every other call's remaining
budget too.
"""
import concurrent.futures
import logging
import time
from typing import Any, Dict, List, Protocol, Tuple

from prompt_naming import build_guideline_prompt_body, guideline_prompt_name

logger = logging.getLogger(__name__)

PROMPT_CALL_TIMEOUT = 30  # seconds per create/get call


class PromptStore(Protocol):
    def get_prompt(self, name: str) -> Any:
        ...

    def create_prompt(self, **kwargs: Any) -> Any:
        ...


def run_with_timeout(func: Any, timeout: float = PROMPT_CALL_TIMEOUT, *args: Any, **kwargs: Any) -> Any:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"{getattr(func, '__name__', func)} exceeded {timeout}s timeout")


def sanitize_label(text: str, max_length: int = 36) -> str:
    """Sanitize text to Langfuse label rules: lowercase alphanumeric with
    underscores/hyphens/periods, <= max_length chars, starting alphanumeric.
    """
    if not text:
        return "unknown"
    sanitized = text.lower().replace(" ", "_")
    sanitized = "".join(c for c in sanitized if c.isalnum() or c in "_-.")
    sanitized = sanitized.strip("_-.")
    if sanitized and not sanitized[0].isalnum():
        sanitized = "item_" + sanitized
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip("_-.")
    return sanitized or "unknown"


def _get_existing(prompt_store: PromptStore, name: str) -> Any:
    try:
        return run_with_timeout(prompt_store.get_prompt, PROMPT_CALL_TIMEOUT, name)
    except Exception:  # noqa: BLE001 -- prompt genuinely may not exist yet
        return None


def sync_guidelines(
    prompt_store: PromptStore,
    scenario_id: int,
    guidelines: List[Dict[str, Any]],
    deprecated: bool = False,
) -> Tuple[List[str], List[str]]:
    """Create/update (or deprecate) each guideline's own prompt.

    Returns ``(successful_names, failed_names)``.
    """
    successful: List[str] = []
    failed: List[str] = []

    for guideline in guidelines:
        prompt_name = guideline_prompt_name(scenario_id, guideline)
        try:
            existing = _get_existing(prompt_store, prompt_name)
            labels = [guideline.get("type", "text"), "production"]

            if deprecated:
                if existing is None:
                    continue
                tags = sorted(set(getattr(existing, "tags", []) + ["deprecated"]))
                run_with_timeout(
                    prompt_store.create_prompt,
                    PROMPT_CALL_TIMEOUT,
                    name=prompt_name,
                    prompt=existing.prompt,
                    type="text",
                    labels=labels,
                    tags=tags,
                )
            else:
                run_with_timeout(
                    prompt_store.create_prompt,
                    PROMPT_CALL_TIMEOUT,
                    name=prompt_name,
                    prompt=build_guideline_prompt_body(guideline),
                    type="text",
                    labels=labels,
                    config={"guideline_id": guideline["id"], "priority": guideline.get("priority", 0)},
                )
            successful.append(prompt_name)
            time.sleep(0.02)  # polite delay between calls
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync guideline prompt %s: %s", prompt_name, exc)
            failed.append(prompt_name)

    return successful, failed


def sync_combinations(
    prompt_store: PromptStore,
    combinations: List[Dict[str, Any]],
    deprecated: bool = False,
) -> Tuple[List[str], List[str]]:
    """Create/update (or deprecate) each computed combination's prompt.

    Returns ``(successful_names, failed_names)``.
    """
    successful: List[str] = []
    failed: List[str] = []

    for combo in combinations:
        prompt_name = combo["combination_name"]
        try:
            existing = _get_existing(prompt_store, prompt_name)

            if deprecated:
                if existing is None:
                    continue
                tags = sorted(set(getattr(existing, "tags", []) + ["deprecated"]))
                run_with_timeout(
                    prompt_store.create_prompt,
                    PROMPT_CALL_TIMEOUT,
                    name=prompt_name,
                    prompt=existing.prompt,
                    type="text",
                    labels=["combination", "production"],
                    tags=tags,
                )
            else:
                run_with_timeout(
                    prompt_store.create_prompt,
                    PROMPT_CALL_TIMEOUT,
                    name=prompt_name,
                    prompt=combo["prompt_text"],
                    type="text",
                    labels=["combination", "production"],
                    config={
                        "scenario_id": combo["scenario_id"],
                        "queue_id": combo["queue_id"],
                        "guideline_group_ids": combo["guideline_group_ids"],
                        "guideline_ids": combo["guideline_ids"],
                    },
                )
            successful.append(prompt_name)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to sync combination prompt %s: %s", prompt_name, exc)
            failed.append(prompt_name)

    return successful, failed

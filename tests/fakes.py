"""In-memory fakes shared across this training's test suite.

No real MariaDB, SQS, or Langfuse connection is ever needed to run these
tests -- every handler takes its dependencies as explicit arguments (or,
for the MariaDB connection, an object satisfying the same `cursor()`
protocol pymysql connections do), so a fake stands in for each one.
"""
import copy
import re
from typing import Any, Dict, List, Optional, Tuple


def default_tables() -> Dict[str, List[dict]]:
    """A fresh copy of the same fixture data as init.sql, for tests."""
    return {
        "queue": [{"id": 1, "name": "Billing Support", "external_queue_code": "BILLING"}],
        "scenario": [{"id": 1, "queue_id": 1, "name": "refund_request"}],
        "guideline_group": [
            {"id": 1, "scenario_id": 1, "tier": 1, "name": "Friendly Tone"},
            {"id": 2, "scenario_id": 1, "tier": 1, "name": "Formal Tone"},
            {"id": 3, "scenario_id": 1, "tier": 2, "name": "Standard Policy"},
            {"id": 4, "scenario_id": 1, "tier": 2, "name": "Promotional Policy"},
        ],
        "guideline": [
            {"id": 1, "guideline_group_id": 1, "type": "text", "guideline_text": "Be warm.", "priority": 10},
            {"id": 2, "guideline_group_id": 2, "type": "text", "guideline_text": "Be formal.", "priority": 10},
            {"id": 3, "guideline_group_id": 3, "type": "text", "priority": 20,
             "guideline_text": "Standard refund terms."},
            {"id": 4, "guideline_group_id": 3, "type": "toggle", "priority": 30,
             "guideline_text": "Offer loyalty credit."},
            {"id": 5, "guideline_group_id": 4, "type": "text", "priority": 20,
             "guideline_text": "Mention promo offer."},
        ],
    }


class FakeCursor:
    """Recognizes the small, fixed set of SQL shapes db_utils.py issues and
    answers them from in-memory fixture tables. Not a general SQL engine --
    intentionally just enough to exercise every branch in db_utils.py.
    """

    def __init__(self, tables: Dict[str, List[dict]]) -> None:
        self.tables = tables
        self._rows: List[dict] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, query: str, params: Tuple[Any, ...] = ()) -> None:
        q = re.sub(r"\s+", " ", query).strip()

        def rows_where(table: str, predicate: Any) -> List[dict]:
            return [copy.deepcopy(r) for r in self.tables.get(table, []) if predicate(r)]

        if "FROM guideline_group WHERE scenario_id = %s AND (tier != %s OR id = %s)" in q:
            scenario_id, tier, item_id = params

            def _matches(r: dict) -> bool:
                return r["scenario_id"] == scenario_id and (r["tier"] != tier or r["id"] == item_id)

            self._rows = rows_where("guideline_group", _matches)
        elif "FROM guideline_group WHERE scenario_id IN" in q:
            self._rows = rows_where("guideline_group", lambda r: r["scenario_id"] in params)
        elif "FROM guideline_group WHERE id = %s" in q:
            self._rows = rows_where("guideline_group", lambda r: r["id"] == params[0])
        elif "FROM guideline WHERE guideline_group_id IN" in q:
            self._rows = rows_where("guideline", lambda r: r["guideline_group_id"] in params)
        elif "FROM guideline WHERE id = %s" in q:
            self._rows = rows_where("guideline", lambda r: r["id"] == params[0])
        elif "FROM scenario WHERE queue_id = %s" in q:
            self._rows = rows_where("scenario", lambda r: r["queue_id"] == params[0])
        elif "FROM scenario WHERE id = %s" in q:
            self._rows = rows_where("scenario", lambda r: r["id"] == params[0])
        else:
            raise AssertionError(f"FakeCursor doesn't recognize query: {q}")

    def fetchall(self) -> List[dict]:
        return self._rows

    def fetchone(self) -> Optional[dict]:
        return self._rows[0] if self._rows else None


class FakeMariaDBConnection:
    def __init__(self, tables: Optional[Dict[str, List[dict]]] = None) -> None:
        self.tables = tables if tables is not None else default_tables()

    def cursor(self, *args: Any, **kwargs: Any) -> FakeCursor:
        return FakeCursor(self.tables)


class FakeSqsClient:
    def __init__(self) -> None:
        self.sent_messages: List[Dict[str, Any]] = []

    def send_message(self, **kwargs: Any) -> Dict[str, Any]:
        self.sent_messages.append(kwargs)
        return {"MessageId": f"msg-{len(self.sent_messages)}"}


class FakePrompt:
    def __init__(self, prompt: str, tags: Optional[List[str]] = None) -> None:
        self.prompt = prompt
        self.tags = tags or []


class FakePromptStore:
    """Stands in for the Langfuse client's get_prompt/create_prompt/flush."""

    def __init__(self) -> None:
        self.prompts: Dict[str, FakePrompt] = {}
        self.create_calls: List[Dict[str, Any]] = []
        self.flushed = False

    def get_prompt(self, name: str) -> FakePrompt:
        if name not in self.prompts:
            raise KeyError(f"No prompt named {name}")
        return self.prompts[name]

    def create_prompt(self, **kwargs: Any) -> FakePrompt:
        self.create_calls.append(kwargs)
        prompt = FakePrompt(kwargs["prompt"], kwargs.get("tags", []))
        self.prompts[kwargs["name"]] = prompt
        return prompt

    def flush(self) -> None:
        self.flushed = True

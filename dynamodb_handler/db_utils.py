"""Cascading MariaDB reads for the "support guideline" hierarchy:
queue -> scenario -> guideline_group (tiered) -> guideline.

Each function answers "what data must be re-read from MariaDB in order to
regenerate every prompt combination affected by a change to this one row?"
without over-fetching the whole hierarchy every time.

The key insight (mirrored from the real system this trains on) is in
``fetch_related_data``'s ``guideline_group``/``guideline`` branches: a
"combination" is one guideline_group per tier, cross-multiplied across
tiers. So when one guideline_group changes, you only need *that* group
(fixed) plus every group from every *other* tier (to rebuild the cross
product) -- you never need that group's same-tier siblings, since those
belong to entirely separate combinations untouched by this change.
"""
import logging
from typing import Any, Dict, List

from pymysql.cursors import DictCursor

logger = logging.getLogger(__name__)


def fetch_scenario_ids_for_queue(mariadb_conn: Any, queue_id: int) -> List[int]:
    with mariadb_conn.cursor() as cursor:
        cursor.execute("SELECT id FROM scenario WHERE queue_id = %s", (queue_id,))
        return [row["id"] for row in cursor.fetchall()]


def fetch_scenario_and_queue(mariadb_conn: Any, scenario_id: int) -> Dict[str, Any]:
    """Return ``{"scenario": {...}, "queue": {...}}`` for one scenario."""
    with mariadb_conn.cursor(DictCursor) as cursor:
        cursor.execute("SELECT * FROM scenario WHERE id = %s", (scenario_id,))
        scenario = cursor.fetchone()
        queue = None
        if scenario:
            cursor.execute("SELECT * FROM queue WHERE id = %s", (scenario["queue_id"],))
            queue = cursor.fetchone()
        return {"scenario": scenario, "queue": queue}


def _fetch_groups(cursor: Any, scenario_ids: List[int]) -> List[dict]:
    if not scenario_ids:
        return []
    placeholders = ",".join(["%s"] * len(scenario_ids))
    cursor.execute(f"SELECT * FROM guideline_group WHERE scenario_id IN ({placeholders})", tuple(scenario_ids))
    return cursor.fetchall()


def _fetch_guidelines(cursor: Any, group_ids: List[int]) -> List[dict]:
    if not group_ids:
        return []
    placeholders = ",".join(["%s"] * len(group_ids))
    cursor.execute(f"SELECT * FROM guideline WHERE guideline_group_id IN ({placeholders})", tuple(group_ids))
    return cursor.fetchall()


def fetch_related_data(item_id: int, table_name: str, mariadb_conn: Any) -> Dict[str, List[dict]]:
    """Fetch every row needed to regenerate all prompt combinations touched
    by a change to ``item_id`` in ``table_name``.

    Returns a dict with ``scenarios``, ``guideline_groups``, ``guidelines``
    (each a list of dict rows). Raises ``ValueError`` for an unrecognized
    ``table_name``.
    """
    result: Dict[str, List[dict]] = {"scenarios": [], "guideline_groups": [], "guidelines": []}

    with mariadb_conn.cursor(DictCursor) as cursor:
        if table_name == "queue":
            cursor.execute("SELECT * FROM scenario WHERE queue_id = %s", (item_id,))
            scenarios = cursor.fetchall()
            result["scenarios"] = scenarios

            scenario_ids = [s["id"] for s in scenarios]
            groups = _fetch_groups(cursor, scenario_ids)
            result["guideline_groups"] = groups
            result["guidelines"] = _fetch_guidelines(cursor, [g["id"] for g in groups])

        elif table_name == "scenario":
            cursor.execute("SELECT * FROM scenario WHERE id = %s", (item_id,))
            result["scenarios"] = cursor.fetchall()

            groups = _fetch_groups(cursor, [item_id])
            result["guideline_groups"] = groups
            result["guidelines"] = _fetch_guidelines(cursor, [g["id"] for g in groups])

        elif table_name == "guideline_group":
            cursor.execute("SELECT * FROM guideline_group WHERE id = %s", (item_id,))
            changed_groups = cursor.fetchall()
            if changed_groups:
                scenario_id = changed_groups[0]["scenario_id"]
                changed_tier = changed_groups[0]["tier"]
                # This group, plus every group from every *other* tier in the
                # same scenario -- not this group's same-tier siblings.
                cursor.execute(
                    "SELECT * FROM guideline_group WHERE scenario_id = %s AND (tier != %s OR id = %s) "
                    "ORDER BY tier",
                    (scenario_id, changed_tier, item_id),
                )
                groups = cursor.fetchall()
                result["guideline_groups"] = groups
                result["guidelines"] = _fetch_guidelines(cursor, [g["id"] for g in groups])
                cursor.execute("SELECT * FROM scenario WHERE id = %s", (scenario_id,))
                result["scenarios"] = cursor.fetchall()

        elif table_name == "guideline":
            cursor.execute("SELECT * FROM guideline WHERE id = %s", (item_id,))
            changed_guideline = cursor.fetchone()
            if changed_guideline:
                group_id = changed_guideline["guideline_group_id"]
                cursor.execute("SELECT * FROM guideline_group WHERE id = %s", (group_id,))
                group = cursor.fetchone()
                if group:
                    scenario_id = group["scenario_id"]
                    changed_tier = group["tier"]
                    cursor.execute(
                        "SELECT * FROM guideline_group WHERE scenario_id = %s AND (tier != %s OR id = %s) "
                        "ORDER BY tier",
                        (scenario_id, changed_tier, group_id),
                    )
                    groups = cursor.fetchall()
                    result["guideline_groups"] = groups
                    result["guidelines"] = _fetch_guidelines(cursor, [g["id"] for g in groups])
                    cursor.execute("SELECT * FROM scenario WHERE id = %s", (scenario_id,))
                    result["scenarios"] = cursor.fetchall()

        else:
            raise ValueError(f"Unsupported table: {table_name}")

    return result

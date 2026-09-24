import pytest
from db_utils import fetch_related_data
from fakes import FakeMariaDBConnection


def test_fetch_related_data_queue_pulls_whole_hierarchy() -> None:
    conn = FakeMariaDBConnection()
    result = fetch_related_data(1, "queue", conn)
    assert [s["id"] for s in result["scenarios"]] == [1]
    assert {g["id"] for g in result["guideline_groups"]} == {1, 2, 3, 4}
    assert {g["id"] for g in result["guidelines"]} == {1, 2, 3, 4, 5}


def test_fetch_related_data_scenario_pulls_its_groups_and_guidelines() -> None:
    conn = FakeMariaDBConnection()
    result = fetch_related_data(1, "scenario", conn)
    assert [s["id"] for s in result["scenarios"]] == [1]
    assert {g["id"] for g in result["guideline_groups"]} == {1, 2, 3, 4}
    assert {g["id"] for g in result["guidelines"]} == {1, 2, 3, 4, 5}


def test_fetch_related_data_guideline_group_excludes_same_tier_siblings() -> None:
    conn = FakeMariaDBConnection()
    # group 1 is tier 1; changing it needs itself plus every *other-tier*
    # group (3, 4) -- not its tier-1 sibling (2).
    result = fetch_related_data(1, "guideline_group", conn)
    assert {g["id"] for g in result["guideline_groups"]} == {1, 3, 4}
    assert {g["id"] for g in result["guidelines"]} == {1, 3, 4, 5}
    assert [s["id"] for s in result["scenarios"]] == [1]


def test_fetch_related_data_guideline_promotes_to_owning_group_scope() -> None:
    conn = FakeMariaDBConnection()
    # guideline 4 belongs to group 3 (tier 2); same exclusion rule applies
    # at the group's tier.
    result = fetch_related_data(4, "guideline", conn)
    assert {g["id"] for g in result["guideline_groups"]} == {1, 2, 3}
    assert {g["id"] for g in result["guidelines"]} == {1, 2, 3, 4}


def test_fetch_related_data_unsupported_table_raises() -> None:
    conn = FakeMariaDBConnection()
    with pytest.raises(ValueError):
        fetch_related_data(1, "not_a_real_table", conn)


def test_fetch_related_data_missing_row_returns_empty_result() -> None:
    conn = FakeMariaDBConnection()
    result = fetch_related_data(999, "guideline_group", conn)
    assert result == {"scenarios": [], "guideline_groups": [], "guidelines": []}

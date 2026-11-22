import pytest
from fastapi import HTTPException

from utsu_core.pagination import paginate_items


def test_paginate_items_orders_and_continues_without_duplicates():
    rows = [{"id": "c"}, {"id": "a"}, {"id": "b"}]

    first, cursor = paginate_items(rows, "", 2, lambda row: (row["id"],), (str,))
    second, next_cursor = paginate_items(rows, cursor, 2, lambda row: (row["id"],), (str,))

    assert [row["id"] for row in first] == ["a", "b"]
    assert [row["id"] for row in second] == ["c"]
    assert cursor and next_cursor is None


@pytest.mark.parametrize("cursor,limit", [("invalid!", 1), ("", 0), ("", 201)])
def test_paginate_items_rejects_invalid_cursor_and_limits(cursor, limit):
    with pytest.raises(HTTPException) as error:
        paginate_items([{"id": "a"}], cursor, limit, lambda row: (row["id"],), (str,))
    assert error.value.status_code == 422

from core.services.roster import division_code, get_trunc_id


def split_multi(value: str) -> list[str]:
    """
    Split a ';' separated multi-select answer.

    >>> split_multi("A; B;;C ")
    ['A', 'B', 'C']
    """
    return [part.strip() for part in (value or "").split(";") if part.strip()]

"""Safety helpers for CSV files that may be opened in spreadsheet software."""


_FORMULA_PREFIXES = ('=', '+', '-', '@')


def spreadsheet_safe_cell(value):
    """Prevent text cells from being interpreted as spreadsheet formulas."""
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value

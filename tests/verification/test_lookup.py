from pathlib import Path

from utsu_core.verification import construct_lookup_tables, setup_reader


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_lookup_supports_commas_and_spacing(tmp_path):
    mapping = write(tmp_path / "map.csv",
                    '"Lookup Key", "Replacement Key"\n'
                    '"Name, as on ACORN", "full_name"\n'
                    '"Plain", "plain"\n'
                    'Unquoted,unq\n')
    names, tables = construct_lookup_tables(mapping)
    assert names == {"Name, as on ACORN": "full_name", "Plain": "plain", "Unquoted": "unq"}
    assert tables["full_name"] == "Name, as on ACORN"


def test_setup_reader_renames_and_keeps_unmapped_columns_aligned(tmp_path):
    mapping = write(tmp_path / "map.csv", '"k", "v"\n"Name, full", "full_name"\n"Division", "division"\n')
    form = write(tmp_path / "form.csv", '"Name, full","Other","Division"\n"Ann Lee","x","UC - University College"\n')
    rows = list(setup_reader(form, mapping))
    assert rows == [{"full_name": "Ann Lee", "Other": "x", "division": "UC - University College"}]

import ast
from pathlib import Path


def _keys_fn():
    """`_keys` is a closure inside `_statement` (the script imports the app and a database): compile just that function."""
    src = (Path(__file__).parents[2] / "scripts" / "r6_case_dump.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "_keys":
            module = ast.Module(body=[node], type_ignores=[])
            ns: dict = {}
            exec(compile(ast.fix_missing_locations(module), "r6_case_dump_keys", "exec"), ns)
            return ns["_keys"]
    raise AssertionError("_keys not found")


def test_keys_emit_entry_group_and_settlement_fingerprint_never_amounts():
    keys = _keys_fn()
    cands = [{"entry_id": 5}, {"group_id": 7}, {"fingerprint": "cathay:2026-09-10:aa", "net_amount": "123.00", "items": []}, "junk"]
    assert keys(cands) == [5, "cathay:2026-09-10:aa", "g7"]
    assert keys(None) == []

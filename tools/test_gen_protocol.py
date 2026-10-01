"""Tests for tools/gen_protocol.py. Standard library plus pytest; never touches the committed generated files
(everything that writes works in a pytest tmp_path copy of the repository's protocol/ and tools/)."""
import copy
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

import gen_protocol as gen

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "gen_protocol.py"
FIXTURES = ROOT / "protocol" / "v1" / "fixtures"


def model():
    return json.loads((ROOT / gen.SOURCE).read_text())


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def repo(tmp_path):
    """A scratch repository root holding a copy of protocol.json, with the generated files already written."""
    (tmp_path / "protocol").mkdir()
    shutil.copy(ROOT / gen.SOURCE, tmp_path / gen.SOURCE)
    assert gen.run(tmp_path, check=False, out=open(os.devnull, "w")) == 0
    return tmp_path


def run_cli(root, *args, hashseed="0"):
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args], capture_output=True, text=True, env=env)


# ---- schema sanity -----------------------------------------------------------------------------------------
def test_the_committed_protocol_json_is_valid():
    assert gen.validate(model()) == []


def cfg(m, name):
    return next(k for k in m["config_keys"] if k["name"] == name)


def mutate(fn):
    m = copy.deepcopy(model())
    fn(m)
    return gen.validate(m)


@pytest.mark.parametrize("name, fn, expect", [
    ("duplicate error code", lambda m: m["error_codes"].append(dict(m["error_codes"][0])), "duplicate error code"),
    ("duplicate exception class", lambda m: m["error_codes"][1].update(remote_exception="NotReady"), "duplicate exception class"),
    ("internal removed", lambda m: m["error_codes"].pop(10), "'internal' is required"),
    ("response code without exception", lambda m: m["error_codes"][0].update(remote_exception=None), "needs a remote_exception"),
    ("code that is not an identifier", lambda m: m["error_codes"][0].update(code="Not Ready"), "lower-case identifier"),
    ("unknown limit ctype", lambda m: m["limits"][0].update(ctype="uint128"), "unknown 'uint128'"),
    ("limit does not fit its type", lambda m: m["limits"][0].update(value=2**40), "does not fit uint32"),
    ("pre-auth cap above the frame cap", lambda m: m["limits"][1].update(value=2**24 + 1), "pre_auth_frame_bytes must not exceed"),
    ("frame cap above the 4-byte prefix", lambda m: m["limits"][0].update(ctype="size", value=2**32), "4-byte length prefix"),
    ("required limit removed", lambda m: m["limits"].pop(3), "'max_batch_calls' is required"),
    ("duplicate limit", lambda m: m["limits"].append(dict(m["limits"][0])), "duplicate limit"),
    ("required limit max_array_items removed", lambda m: m["limits"].pop(-1), "'max_array_items' is required"),
    ("config default outside its range", lambda m: cfg(m, "port").update(default=70000), "outside 0..65535"),
    ("default timeout outside the timeout limits", lambda m: cfg(m, "default_timeout_ms").update(max=7200000), "inside the timeout limits"),
    ("unknown config type", lambda m: cfg(m, "port").update(type="float"), "unknown 'float'"),
    ("bool config key with a non-bool default", lambda m: cfg(m, "bridge_enabled").update(default="no"), "must be a boolean"),
    ("unknown argument type", lambda m: m["ops"][2]["args"][0].update(type="blob"), "unknown argument type"),
    ("duplicate op", lambda m: m["ops"].append(dict(m["ops"][0])), "duplicate op"),
    ("op without needs_game", lambda m: m["ops"][0].pop("needs_game"), "missing 'needs_game'"),
    ("op name without a dot", lambda m: m["ops"][0].update(name="gamestate"), "dotted lower-case op name"),
    ("exactly_one_of names a missing argument", lambda m: m["ops"][1].update(exactly_one_of=["nope"]), "is not an argument"),
    ("exactly_one_of names a required argument", lambda m: m["ops"][1]["args"][0].update(required=True), "must be optional"),
    ("non-ASCII documentation", lambda m: m["limits"][0].update(doc="café"), "non-ASCII"),
    ("default alias type not an alias type", lambda m: m.update(default_alias_type="MagicAlias"), "default_alias_type"),
    ("light extension not in the list", lambda m: m["form_ids"].update(light_extension=".esx"), "light_extension"),
    ("negative protocol version", lambda m: m["protocol"].update(major=-1), "out of range"),
    ("unknown format", lambda m: m.update(format=2), "format 1 only"),
])
def test_schema_problems_are_reported(name, fn, expect):
    problems = mutate(fn)
    assert any(expect in p for p in problems), (name, problems)


def test_an_unreadable_source_is_a_clean_exit_2(tmp_path):
    (tmp_path / "protocol").mkdir()
    (tmp_path / gen.SOURCE).write_text("{not json")
    r = run_cli(tmp_path)
    assert r.returncode == 2 and "invalid" in r.stderr


def test_an_invalid_schema_prints_every_problem_and_exits_2(repo):
    m = model()
    m["error_codes"].append(dict(m["error_codes"][0]))
    m["limits"][0]["ctype"] = "nope"
    (repo / gen.SOURCE).write_text(json.dumps(m))
    r = run_cli(repo, "--check")
    assert r.returncode == 2
    assert "duplicate error code" in r.stderr and "unknown 'nope'" in r.stderr


# ---- output shape and determinism --------------------------------------------------------------------------
def test_every_output_carries_the_banner_and_the_source_name():
    for rel, text in gen.render_all(model()).items():
        head = "\n".join(text.splitlines()[:3])
        assert "GENERATED FILE - DO NOT EDIT" in head and "protocol/protocol.json" in head, rel
        assert text.endswith("\n") and "\r" not in text and "\t" not in text, rel


def test_rendering_is_deterministic_in_process():
    assert gen.render_all(model()) == gen.render_all(copy.deepcopy(model()))


def test_output_bytes_do_not_depend_on_hash_randomisation(tmp_path):
    outs = []
    for seed in ("0", "1", "12345"):
        root = tmp_path / f"seed{seed}"
        (root / "protocol").mkdir(parents=True)
        shutil.copy(ROOT / gen.SOURCE, root / gen.SOURCE)
        r = run_cli(root, hashseed=seed)
        assert r.returncode == 0, r.stderr
        outs.append({rel: (root / rel).read_bytes() for rel in gen.OUTPUTS.values()})
    assert outs[0] == outs[1] == outs[2]


def test_a_second_run_changes_nothing(repo):
    r = run_cli(repo)
    assert r.returncode == 0
    assert r.stdout.count("unchanged") == 3 and "wrote" not in r.stdout


def test_error_code_order_is_the_declaration_order_not_alphabetical():
    m = model()
    header = gen.render_cpp(m)
    pos = [header.index(f"    {c['code']},") for c in m["error_codes"]]
    assert pos == sorted(pos)
    assert [c["code"] for c in m["error_codes"]] != sorted(c["code"] for c in m["error_codes"])


def test_the_generated_python_module_matches_the_source():
    ns = {}
    exec(compile(gen.render_py(model()), "_protocol_generated.py", "exec"), ns)
    m = model()
    assert ns["PROTOCOL_VERSION"] == (m["protocol"]["major"], m["protocol"]["minor"])
    assert ns["ERROR_CODES"] == tuple(c["code"] for c in m["error_codes"])
    for l in m["limits"]:
        assert ns[l["name"].upper()] == l["value"], l["name"]
    assert set(ns["OPS"]) == {o["name"] for o in m["ops"]}
    assert set(ns["REMOTE_EXCEPTIONS"]) | set(ns["HANDSHAKE_EXCEPTIONS"]) <= set(ns["ERROR_CODES"])
    assert ns["BATCHABLE_OPS"] == tuple(o["name"] for o in m["ops"] if o["batchable"])
    assert {n: o["needs_game"] for n, o in ns["OPS"].items()} == {o["name"]: o["needs_game"] for o in m["ops"]}
    assert set(ns["CONFIG_KEYS"]) == {k["name"] for k in m["config_keys"]}


@pytest.mark.skipif(shutil.which("g++") is None, reason="no C++ compiler on this machine")
def test_the_generated_header_compiles_standalone(tmp_path):
    (tmp_path / "t.cpp").write_text('#include "spy/protocol_generated.h"\n'
                                    "static_assert(spy::kErrorCodeNames.size() > 0);\n"
                                    "static_assert(spy::find_op(spy::ops::kGameState) != nullptr);\n"
                                    "static_assert(!spy::find_op(spy::ops::kGameState)->needs_game);\n"
                                    "static_assert(spy::find_op(spy::ops::kPapyrusCallStatic)->needs_game);\n"
                                    "static_assert(spy::kMaxArrayItems == 128u);\n"
                                    "static_assert(!spy::config_defaults::kBridgeEnabled && spy::config_defaults::kPythonEnabled);\n"
                                    "int main() { return 0; }\n")
    inc = tmp_path / "inc" / "spy"
    inc.mkdir(parents=True)
    (inc / "protocol_generated.h").write_text(gen.render_cpp(model()))
    r = subprocess.run(["g++", "-std=c++20", "-Wall", "-Wextra", "-Wconversion", "-pedantic", "-Werror", "-I", str(tmp_path / "inc"),
                        "-fsyntax-only", str(tmp_path / "t.cpp")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---- the drift check ---------------------------------------------------------------------------------------
def test_check_passes_on_fresh_output(repo):
    r = run_cli(repo, "--check")
    assert r.returncode == 0 and "up to date" in r.stdout


@pytest.mark.parametrize("rel", sorted(gen.OUTPUTS.values()))
def test_mutating_a_generated_file_fails_the_check_and_shows_a_diff(repo, rel):
    path = repo / rel
    text = path.read_text()
    marker = {"core": "16777216u", "python": "16777216", "protocol": "16 777 216"}[rel.split("/")[0]]
    assert marker in text
    path.write_text(text.replace(marker, marker.replace("16", "17", 1), 1))
    r = run_cli(repo, "--check")
    assert r.returncode == 1
    assert f"DRIFT: {rel}" in r.stderr and "\n-" in r.stderr and "\n+" in r.stderr
    assert "(committed)" in r.stderr and "(generated)" in r.stderr
    assert run_cli(repo).returncode == 0 and run_cli(repo, "--check").returncode == 0  # regenerating repairs it


def test_a_missing_generated_file_fails_the_check(repo):
    target = repo / gen.OUTPUTS["md"]
    target.rename(target.with_suffix(".moved"))
    r = run_cli(repo, "--check")
    assert r.returncode == 1 and "MISSING" in r.stderr


def test_editing_the_source_without_regenerating_fails_the_check(repo):
    m = model()
    limit = next(l for l in m["limits"] if l["name"] == "max_batch_calls")
    limit["value"] += 1
    (repo / gen.SOURCE).write_text(json.dumps(m, indent=2))
    r = run_cli(repo, "--check")
    assert r.returncode == 1 and "kMaxBatchCalls" in r.stderr
    assert run_cli(repo).returncode == 0
    assert f"kMaxBatchCalls = {limit['value']}u" in (repo / gen.OUTPUTS["cpp"]).read_text()
    assert run_cli(repo, "--check").returncode == 0


def test_crlf_line_endings_from_a_windows_checkout_are_not_drift(repo):
    for rel in gen.OUTPUTS.values():
        p = repo / rel
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert run_cli(repo, "--check").returncode == 0


def test_check_never_writes(repo):
    rel = gen.OUTPUTS["py"]
    (repo / rel).write_text("# tampered\n")
    run_cli(repo, "--check")
    assert (repo / rel).read_text() == "# tampered\n"


def test_the_committed_generated_files_are_current():
    """The real drift check, run against the real repository (this is the test that fails when someone edits
    protocol.json, or a generated file, and forgets the other half)."""
    assert gen.run(ROOT, check=True, out=open(os.devnull, "w"), err=sys.stderr) == 0


# ---- rules the index's Contracts fix ------------------------------------------------------------------------
def test_only_game_state_works_without_a_game():
    assert [o["name"] for o in model()["ops"] if not o["needs_game"]] == ["game.state"]


def test_the_config_keys_are_the_plugin_config_fields_with_their_defaults():
    keys = {k["name"]: k["default"] for k in model()["config_keys"]}
    assert keys == {"python_enabled": True, "scripts_enabled": True, "bridge_enabled": False, "port": 45977, "fixed_token": "",
                    "session_file": "", "default_timeout_ms": 5000, "max_inflight_per_connection": 256,
                    "max_inflight_total": 1024, "enable_console": False}


# ---- protocol.json agrees with the golden fixtures ---------------------------------------------------------
def test_fixture_error_codes_are_in_the_table():
    codes = {c["code"] for c in model()["error_codes"]}
    seen = set()
    for name in ("hello.json", "requests.json", "values.json"):
        seen |= {e["error"] for e in fixture(name)["invalid"]}
    seen |= {c["result"] for c in fixture("hello.json")["check"] if c["result"]}
    for r in fixture("responses.json")["valid"]:
        if r.get("ok") is False:
            seen.add(r["error"]["code"])
        if r.get("type") == "handshake_error":
            seen.add(r["code"])
    assert seen and seen <= codes, seen - codes
    handshake = {c["code"] for c in model()["error_codes"] if c["context"] in ("handshake", "both")}
    assert {c["result"] for c in fixture("hello.json")["check"] if c["result"]} <= handshake


def test_every_op_has_a_valid_fixture_and_fixtures_use_only_known_argument_names():
    ops = {o["name"]: o for o in model()["ops"]}
    seen = {name: set() for name in ops}
    for req in fixture("requests.json")["valid"]:
        assert req["op"] in ops, req["op"]
        names = {a["name"] for a in ops[req["op"]]["args"]}
        assert set(req["args"]) <= names, (req["op"], set(req["args"]) - names)
        seen[req["op"]] |= set(req["args"])
        if req["op"] == "papyrus.batch":
            for step in req["args"]["calls"]:
                assert ops[step["op"]]["batchable"], step["op"]
                assert set(step["args"]) <= {a["name"] for a in ops[step["op"]]["args"]}
    for name, o in ops.items():
        assert seen[name] or not o["args"], f"no valid fixture exercises {name}"
        assert seen[name] >= {a["name"] for a in o["args"] if a["required"]}, f"{name}: no fixture sets every required argument"


def test_fixtures_pin_the_limits_in_the_table_to_the_byte():
    lim = {l["name"]: l["value"] for l in model()["limits"]}
    timeouts = {r["message"]["args"]["timeout_ms"] for r in fixture("requests.json")["invalid"]
                if isinstance(r["message"].get("args"), dict) and "timeout_ms" in r["message"]["args"]}
    assert lim["min_timeout_ms"] - 1 in timeouts and lim["max_timeout_ms"] + 1 in timeouts
    valid_t = {r["args"]["timeout_ms"] for r in fixture("requests.json")["valid"] if "timeout_ms" in r["args"]}
    assert valid_t and all(lim["min_timeout_ms"] <= t <= lim["max_timeout_ms"] for t in valid_t)
    wires = [c["wire"] for c in fixture("values.json")["valid"]]
    bad = [c["wire"] for c in fixture("values.json")["invalid"]]
    assert lim["int_max"] in wires and lim["int_min"] in wires and lim["int_max"] + 1 in bad and lim["int_min"] - 1 in bad
    assert lim["float_max_text"] in wires and 3.5e38 in bad and lim["float_max_text"] < 3.5e38
    alias_ids = [c["wire"]["alias_id"] for c in fixture("values.json")["valid"] if isinstance(c["wire"], dict) and c["wire"].get("kind") == "alias"]
    assert lim["int_max"] in alias_ids
    arrays = [c["wire"] for c in fixture("values.json")["valid"] if isinstance(c["wire"], dict) and "array" in c["wire"]]
    long_bad = [c for c in fixture("values.json")["invalid"] if isinstance(c["wire"], dict) and "array" in c["wire"]
                and len(c["wire"]["items"]) > lim["max_array_items"]]
    assert lim["max_array_items"] in [len(a["items"]) for a in arrays]
    assert [len(c["wire"]["items"]) for c in long_bad] == [lim["max_array_items"] + 1] and long_bad[0]["error"] == "type_mismatch"
    hello_tokens = [h["token"] for h in fixture("hello.json")["valid"]]
    assert all(len(t) == lim["token_length"] for t in hello_tokens)
    for h in fixture("hello.json")["valid"]:
        assert h["protocol"][0] == model()["protocol"]["major"]
    f = model()["form_ids"]
    keys = fixture("form_keys.json")
    assert f"Skyrim.esm|0x{f['max_local_id']:X}" in [c["text"] for c in keys["valid"]]
    assert f"Skyrim.esm|0x{f['max_local_id'] + 1:X}" in keys["invalid"]
    assert f"Light Mod.esl|0x{f['max_light_local_id']:X}" in [c["text"] for c in keys["valid"]]
    assert f"Light Mod.esl|0x{f['max_light_local_id'] + 1:X}" in keys["invalid"]


def test_the_fixtures_use_only_known_message_types_and_alias_types():
    m = model()
    for r in fixture("responses.json")["valid"]:
        assert r["type"] in m["message_types"]
    for c in fixture("values.json")["valid"]:
        w = c["wire"]
        if isinstance(w, dict) and w.get("kind") == "alias":
            assert w["alias_type"] in m["alias_types"]
        if isinstance(w, dict) and "array" in w:
            assert w["array"] in m["array_elem_types"]


def test_protocol_md_links_the_generated_tables_and_repeats_no_number_from_them():
    md = (ROOT / "protocol" / "PROTOCOL.md").read_text()
    assert "PROTOCOL.generated.md" in md
    m = model()
    lim = {l["name"]: l["value"] for l in m["limits"]}
    for name, value in lim.items():
        if abs(value) < 32:
            continue  # 1 or 5 would match anywhere
        for text in {str(value), gen.human_bytes(value) if isinstance(value, int) else str(value)}:
            hit = re.search(rf"(?<![\w.]){re.escape(text)}(?![\w.])", md)
            assert not hit, f"PROTOCOL.md repeats {name} ({text!r}); refer to the limit by name instead"
    for c in m["error_codes"]:
        assert f"`{c['code']}` |" not in md, "PROTOCOL.md must not carry its own error-code table"
    assert str(cfg(m, "port")["default"]) not in md  # the default port

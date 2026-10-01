"""Spike S10 check. Runs INSIDE the game's embedded CPython through Task 3's `py_exec`, as
    exec(open(CHECK).read(), {"__name__": "s10", "SITE": ..., "OUT": ..., "URL": ...})
It never raises: every step's result or error goes into the JSON file OUT, which the Linux side reads.
SITE: the deployed site-packages folder. URL: a plain-HTTP URL served from Linux.
The imports run first WITHOUT touching sys.path: they must work through the site-packages entry that
PyLoad.cpp configured (module_search_paths), which is what Task 26 relies on. Only if that pass fails is SITE
inserted at the front and the steps run again, to tell a path problem from a wheel problem."""
import importlib
import importlib.metadata
import json
import os
import ssl
import sys
import time
import traceback

result = {"python": sys.version, "executable": sys.executable, "platform": sys.platform,
          "openssl": ssl.OPENSSL_VERSION, "site": SITE, "steps": {}}


def step(name, fn):
    t0 = time.perf_counter()
    try:
        value = fn()
        result["steps"][name] = {"ok": True, "value": value, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except BaseException as e:  # noqa: BLE001 - a spike records everything, including SystemExit
        result["steps"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                                 "trace": traceback.format_exc(limit=6)}


def _same(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def check_configured_path():
    # Before any insert: is the configured site-packages entry (another spelling of SITE) on sys.path?
    hits = [p for p in sys.path if _same(p, SITE)]
    result["configured_site_in_path"] = bool(hits)
    return {"configured_site_in_path": bool(hits), "entries": hits, "path_head": sys.path[:4]}


def check_httpx_import():
    import httpx
    return httpx.__version__


def check_jsonschema():
    import jsonschema
    import rpds  # the compiled cp314 win_amd64 extension (.pyd)
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    jsonschema.validate({"n": 1}, schema)
    try:
        jsonschema.validate({"n": "x"}, schema)
        raise AssertionError("an invalid document was accepted")
    except jsonschema.ValidationError:
        pass
    return {"jsonschema": importlib.metadata.version("jsonschema"), "rpds": rpds.__file__}


def check_mwparserfromhell():
    import mwparserfromhell
    found = [str(t) for t in mwparserfromhell.parse("x {{a|b}} y").filter_templates()]
    assert found == ["{{a|b}}"], found
    return {"version": mwparserfromhell.__version__, "use_c": mwparserfromhell.parser.use_c}


def check_httpx_get():
    import httpx
    # trust_env=False: no proxy or certificate settings from the player's environment.
    with httpx.Client(timeout=10.0, trust_env=False) as client:  # builds a default SSL context (certifi) too
        r = client.get(URL)
        return {"status": r.status_code, "body": r.text.strip()}


CHECKS = [("import_httpx", check_httpx_import), ("jsonschema_validate", check_jsonschema),
          ("mwparserfromhell_parse", check_mwparserfromhell), ("httpx_get", check_httpx_get)]


def all_ok(suffix=""):
    s = result["steps"]
    return all(s[n + suffix]["ok"] for n, _ in CHECKS) and s["httpx_get" + suffix].get("value", {}).get("body") == "s10-ok"


step("sys_path", check_configured_path)
for name, fn in CHECKS:  # pass 1: the configured sys.path only
    step(name, fn)
result["configured_path_pass"] = all_ok()
if not result["configured_path_pass"]:
    # Diagnosis only: insert SITE and retry in a clean import state (failed imports may have left partial modules).
    for mod in [m for m in sys.modules if m.split(".")[0] in ("httpx", "httpcore", "anyio", "h11", "certifi", "idna", "jsonschema",
                                                               "jsonschema_specifications", "referencing", "rpds", "attrs", "attr",
                                                               "mwparserfromhell", "typing_extensions", "sniffio")]:
        del sys.modules[mod]
    importlib.invalidate_caches()
    sys.path.insert(0, SITE)
    for name, fn in CHECKS:
        step(name + "_with_insert", fn)
    result["with_insert_pass"] = all_ok("_with_insert")
result["pass"] = result["configured_site_in_path"] and result["configured_path_pass"]
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)

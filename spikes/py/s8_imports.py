# S8: stdlib modules a real consumer needs, imported inside the game's embedded interpreter.
import ctypes
import json
import os
import socket
import sqlite3
import ssl
import sys
import threading

_con = sqlite3.connect(":memory:")
_con.execute("create table t(x)")
_con.execute("insert into t values (42)")
_ctx = ssl.create_default_context()
_s = socket.socket()
_s.close()
result = json.dumps({
    "version": sys.version,
    "hexversion": hex(sys.hexversion),          # 3.14.8 final is 0x30e08f0
    "isolated": sys.flags.isolated,             # 1
    "ignore_environment": sys.flags.ignore_environment,  # 1
    "prefix": sys.prefix,
    "executable": sys.executable,
    "path": sys.path,
    "site_imported": "site" in sys.modules,
    "openssl": ssl.OPENSSL_VERSION,
    "sqlite": sqlite3.sqlite_version,
    "sqlite_value": _con.execute("select x from t").fetchone()[0],   # 42
    "pointer_bytes": ctypes.sizeof(ctypes.c_void_p),                 # 8
    "threads": threading.active_count(),
    "stdout": type(sys.stdout).__name__,
    "pythonpath_env": os.environ.get("PYTHONPATH"),
}, sort_keys=True)

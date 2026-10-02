import json
import os

import pytest

from skython import _protocol_generated as g
from skython.discovery import Session, find_session, write_inactive_session_file, write_session_file
from skython.errors import NotRunning

TOK = "00112233445566778899aabbccddeeff"


def write_session(path, port=45977, token=TOK):
    path.write_text(json.dumps({"port": port, "token": token, "pid": 42, "plugin_version": "0.1.0", "protocol": [1, 0]}))
    return path


def test_the_session_file_fields_are_the_tables(tmp_path):
    assert set(json.loads(write_session(tmp_path / "s.json").read_text())) == set(g.SESSION_FILE_FIELDS)
    assert (g.ENV_SESSION, g.ENV_PORT, g.ENV_TOKEN) == ("SKYTHON_SESSION", "SKYTHON_PORT", "SKYTHON_TOKEN")


def test_explicit_port_and_token_win(tmp_path):
    s = find_session(write_session(tmp_path / "s.json", port=1), port=2, token=TOK, env={})
    assert s.port == 2


def test_explicit_session_path(tmp_path):
    s = find_session(write_session(tmp_path / "s.json"), env={})
    assert (s.host, s.port, s.token, s.pid) == ("127.0.0.1", 45977, TOK, 42)


def test_env_session_then_env_port(tmp_path):
    p = write_session(tmp_path / "s.json", port=3)
    assert find_session(env={"SKYTHON_SESSION": str(p)}).port == 3
    assert find_session(env={"SKYTHON_PORT": "4", "SKYTHON_TOKEN": TOK}).port == 4


def test_missing_session_file_is_not_running(tmp_path):
    with pytest.raises(NotRunning, match="no session file"):
        find_session(tmp_path / "absent.json", env={})


def test_nothing_configured_is_not_running():
    with pytest.raises(NotRunning, match="SKYTHON_SESSION"):
        find_session(env={})


def test_corrupt_session_file_is_not_running(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    with pytest.raises(NotRunning, match="unreadable"):
        find_session(p, env={})


@pytest.mark.parametrize("bad", ["abc", "4x", "0", "65536", "-1", "1.5"])
def test_bad_env_port_is_not_running_not_value_error(bad):
    with pytest.raises(NotRunning, match="SKYTHON_PORT is not a port number"):
        find_session(env={"SKYTHON_PORT": bad, "SKYTHON_TOKEN": TOK})


def test_only_half_of_a_pair_is_a_clear_error():
    with pytest.raises(NotRunning, match="SKYTHON_PORT is set but SKYTHON_TOKEN is not"):
        find_session(env={"SKYTHON_PORT": "4"})
    with pytest.raises(NotRunning, match="SKYTHON_TOKEN is set but SKYTHON_PORT is not"):
        find_session(env={"SKYTHON_TOKEN": TOK})
    with pytest.raises(NotRunning, match="together.*only port"):
        find_session(port=5, env={})
    with pytest.raises(NotRunning, match="together.*only token"):
        find_session(token=TOK, env={})


def test_a_session_source_beats_a_lone_env_variable(tmp_path):
    p = write_session(tmp_path / "s.json", port=3)
    assert find_session(env={"SKYTHON_SESSION": str(p), "SKYTHON_PORT": "9"}).port == 3


@pytest.mark.parametrize("port", ["abc", 0, 70000, None, True])
def test_bad_port_in_session_file(tmp_path, port):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"port": port, "token": TOK}))
    with pytest.raises(NotRunning, match="port"):
        find_session(p, env={})


def test_bad_explicit_port_is_not_running():
    with pytest.raises(NotRunning, match="port="):
        find_session(port="abc", token=TOK, env={})


# -- writing session files (the bridge, Task 23) ------------------------------------------------------------
def test_write_session_file_round_trips_and_has_exactly_the_table_fields(tmp_path):
    p = tmp_path / "deeper" / "session.json"  # the folder is created
    write_session_file(p, Session("127.0.0.1", 45999, TOK, pid=7, plugin_version="0.1.0"))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert set(data) == set(g.SESSION_FILE_FIELDS)
    assert data["protocol"] == list(g.PROTOCOL_VERSION)
    s = find_session(p, env={})
    assert (s.port, s.token, s.pid, s.plugin_version) == (45999, TOK, 7, "0.1.0")


def test_write_session_file_replaces_atomically_and_leaves_no_temp_files(tmp_path):
    p = tmp_path / "session.json"
    write_session(p, port=1)
    write_session_file(p, Session("127.0.0.1", 2, TOK))
    assert find_session(p, env={}).port == 2
    assert sorted(os.listdir(tmp_path)) == ["session.json"]


def test_an_inactive_session_file_names_its_reason(tmp_path):
    p = tmp_path / "session.json"
    write_session(p)  # a stale active file from an earlier run is overwritten
    write_inactive_session_file(p, "bridge_enabled is false", pid=9, plugin_version="0.1.0")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"active": False, "reason": "bridge_enabled is false", "pid": 9, "plugin_version": "0.1.0"}
    assert "port" not in data and "token" not in data
    with pytest.raises(NotRunning, match="not running: bridge_enabled is false"):
        find_session(p, env={})


# -- fix round 1: strict session file contents ---------------------------------------------------------------
@pytest.mark.parametrize("port", [1.5, 45977.0, [1], {"a": 1}])
def test_a_port_must_be_an_int_or_a_digit_string(tmp_path, port):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"port": port, "token": TOK}))
    with pytest.raises(NotRunning, match="port"):
        find_session(p, env={})


def test_a_digit_string_port_is_accepted(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"port": "4500", "token": TOK}))
    assert find_session(p, env={}).port == 4500


@pytest.mark.parametrize("token", [None, 12345, ["x"], ""])
def test_a_token_must_be_a_string(tmp_path, token):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"port": 4500, "token": token}))
    with pytest.raises(NotRunning, match="token"):
        find_session(p, env={})


@pytest.mark.parametrize("text", ["[]", "42", "null", '"x"'])
def test_a_session_file_that_is_not_an_object_is_not_running(tmp_path, text):
    p = tmp_path / "s.json"
    p.write_text(text)
    with pytest.raises(NotRunning, match="not a JSON object"):
        find_session(p, env={})

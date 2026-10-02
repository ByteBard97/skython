"""scripts/<name>.toml as a read-only game.settings, and console echo of script failures (fake _skython)."""
import sys
import textwrap

import pytest

from skython.inprocess import connect_in_process
from test_host import host, script  # noqa: F401  (the host fixture and the script writer from test_host.py)

CAPTURE = """
SEEN = {}

def setup(game):
    SEEN["settings"] = game.settings
"""


def settings_file(tmp_path, name, text, package=False):
    folder = tmp_path / "scripts"
    path = folder / name / "settings.toml" if package else folder / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


def run_host(host, fake):
    host.main()
    return host.status()["scripts"]


def test_settings_are_read_and_frozen(host, fake, tmp_path):
    script(tmp_path, "t24_s43_a", CAPTURE)
    settings_file(tmp_path, "t24_s43_a", 'greeting = "hi"\nlimits = [1, 2]\n[nested]\nlevel = 3\n')
    assert run_host(host, fake)["t24_s43_a"] == "ok"
    s = sys.modules["t24_s43_a"].SEEN["settings"]
    assert s["greeting"] == "hi" and s["limits"] == (1, 2) and s["nested"]["level"] == 3
    with pytest.raises(TypeError):
        s["greeting"] = "changed"
    with pytest.raises(TypeError):
        s["nested"]["level"] = 4
    with pytest.raises(AttributeError):
        s["limits"].append(3)


def test_settings_attribute_is_read_only(fake):
    with connect_in_process() as g:
        assert dict(g.settings) == {}           # not a player script's Game: empty
        with pytest.raises(AttributeError):
            g.settings = {"x": 1}


def test_a_script_without_a_file_gets_an_empty_mapping(host, fake, tmp_path):
    script(tmp_path, "t24_s43_b", CAPTURE)
    assert run_host(host, fake)["t24_s43_b"] == "ok"
    assert dict(sys.modules["t24_s43_b"].SEEN["settings"]) == {}


def test_package_settings_and_module_file_wins(host, fake, tmp_path):
    script(tmp_path, "t24_s43_c", CAPTURE, package=True)
    settings_file(tmp_path, "t24_s43_c", "source = 'package'\n", package=True)
    script(tmp_path, "t24_s43_d", CAPTURE, package=True)
    settings_file(tmp_path, "t24_s43_d", "source = 'package'\n", package=True)
    settings_file(tmp_path, "t24_s43_d", "source = 'module file'\n")
    run_host(host, fake)
    assert sys.modules["t24_s43_c"].SEEN["settings"]["source"] == "package"
    assert sys.modules["t24_s43_d"].SEEN["settings"]["source"] == "module file"


def test_a_toml_error_fails_only_that_script_and_skips_setup(host, fake, tmp_path):
    script(tmp_path, "t24_s43_e", CAPTURE)
    settings_file(tmp_path, "t24_s43_e", "this is = = not toml\n")
    script(tmp_path, "t24_s43_f", CAPTURE)
    status = run_host(host, fake)
    assert status["t24_s43_e"].startswith("failed: settings: t24_s43_e.toml:"), status
    assert sys.modules["t24_s43_e"].SEEN == {}  # setup never ran
    assert "t24_s43_e" not in host._STATE["games"]  # its Game was closed and forgotten
    assert status["t24_s43_f"] == "ok"


def test_reload_rereads_the_file(host, fake, tmp_path):
    script(tmp_path, "t24_s43_g", CAPTURE)
    settings_file(tmp_path, "t24_s43_g", "v = 1\n")
    run_host(host, fake)
    settings_file(tmp_path, "t24_s43_g", "v = 2\n")
    assert host.reload("t24_s43_g") == "ok"
    assert sys.modules["t24_s43_g"].SEEN["settings"]["v"] == 2


def test_a_failing_script_echoes_one_console_line(host, fake, tmp_path):
    script(tmp_path, "t24_s43_h", "def setup(game):\n    raise ValueError('boom')\n")
    run_host(host, fake)
    assert fake.console_lines == ["Skython: script t24_s43_h failed: ValueError: boom; see python.log"]


def test_a_timed_out_script_echoes_one_console_line(host, fake, tmp_path):
    script(tmp_path, "t24_s43_j", "import time\n\ndef setup(game):\n    time.sleep(1.0)\n")  # host timeout 0.5 s
    run_host(host, fake)
    assert fake.console_lines == ["Skython: script t24_s43_j timeout: setup still running after 0.5 s; see python.log"]


def test_console_echo_errors_false_echoes_nothing(host, fake, tmp_path):
    fake.config_overrides["console_echo_errors"] = False
    script(tmp_path, "t24_s43_i", "def setup(game):\n    raise ValueError('boom')\n")
    run_host(host, fake)
    assert fake.console_lines == []

"""Task 43 live: the broken script's failure reaches the in-game console once; a script reads its settings.toml."""
import datetime
import re
import subprocess
import time

from smoke import _read
from support import REPO, _launch_stamp, log_text

LINE = "Skython: script livetest_broken failed: RuntimeError: livetest_broken setup raises on purpose; see python.log"


def test_the_broken_script_is_echoed_once_and_the_game_runs_on(game):
    deadline = time.monotonic() + 60
    while (n := log_text().count(f"console: printed {LINE}")) == 0 and time.monotonic() < deadline:
        time.sleep(2)
    assert n == 1, f"expected exactly one console line, found {n}"
    assert game.state()["data_ready"] is True


def test_console_screenshot_for_the_record():
    # Evidence for RESULTS: open the console through DevBench, take a screenshot, close it again.
    menu = [str(REPO / "tools" / "box" / "box_console.sh"), "--tool", "menu"]
    subprocess.run(menu + ['{"action":"open","name":"Console"}'], check=True, capture_output=True, text=True, timeout=90)
    try:
        r = subprocess.run([str(REPO / "tools" / "box" / "box_shot.sh")], check=True, capture_output=True, text=True, timeout=120)
        print(r.stdout)
    finally:
        subprocess.run(menu + ['{"action":"close","name":"Console"}'], check=True, capture_output=True, text=True, timeout=90)


def test_a_script_reads_its_settings_file():
    data = _read("livetest_settings.json")
    # Written by this launch, not left over from an earlier one: compare with the launch stamp (box clock, ISO with offset).
    stamp = re.sub(r"(\.\d{6})\d+", r"\1", _launch_stamp().read_text().strip())
    assert data.pop("written_at") >= datetime.datetime.fromisoformat(stamp).timestamp(), "stale livetest_settings.json"
    assert data == {"settings": {"greeting": "hello from settings.toml", "limits": [1, 2, 3], "nested": {"level": 3}},
                    "frozen": True}, data

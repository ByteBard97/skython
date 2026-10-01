import json
import pathlib

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "protocol" / "v1" / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())

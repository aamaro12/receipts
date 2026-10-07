import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
FIXTURES = ROOT / "tests" / "fixtures"

import pytest


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """No test reaches the Mistral API: the real clients fail before any request without a key."""
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

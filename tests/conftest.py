from pathlib import Path
import json
import pytest
from ac_agent.models import Lap, SessionMeta

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def recorded():
    payload = json.loads((FIXTURES / "demo-recording.json").read_text())
    return SessionMeta.model_validate(payload["meta"]), [
        Lap.model_validate(l) for l in payload["laps"]
    ]


@pytest.fixture(autouse=True)
def isolated_ac_folders(tmp_path_factory, monkeypatch):
    """No test may read or write the real Assetto Corsa folders or use a real
    API key: both locations point to fresh temporary folders."""
    root = tmp_path_factory.mktemp("ac")
    (root / "install" / "content" / "cars").mkdir(parents=True)
    monkeypatch.setenv("AC_AGENT_AC_DIR", str(root / "install"))
    monkeypatch.setenv("AC_AGENT_SETUPS_DIR", str(root / "setups"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return root

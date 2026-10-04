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

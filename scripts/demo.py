"""Exercise the v1 -> v2 traceability flow without a running server."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from ct200.api import app


with TestClient(app) as client:
    delta = client.get("/changes?from_version=v1&to_version=v2").json()
    print("Changed/added/removed nodes:", [(x["status"], x["logical_key"]) for x in delta["changes"] if x["status"] != "unchanged"])
    selection = client.post("/selections", json={"version_id": "v1", "node_keys": ["sec-2.1.1.1"]}).json()
    generated = client.post(f"/selections/{selection['selection_id']}/generations", json={"count": 3, "use_live_llm": False}).json()
    print("Pinned selection:", selection["selection_id"])
    print("Generation:", generated["generation_id"], generated["validation_status"])
    print("Stale after v2:", generated["staleness"]["is_stale"])
    history = client.get(f"/generations?selection_id={selection['selection_id']}").json()
    print("Historical outputs retrieved:", history["count"])

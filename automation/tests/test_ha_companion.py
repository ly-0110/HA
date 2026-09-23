import importlib.util
import io
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "ha_companion.py"
SPEC = importlib.util.spec_from_file_location("ha_companion", SCRIPT)
assert SPEC and SPEC.loader
ha_companion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ha_companion)


def test_companion_exports_entity_history_without_persisting_token(monkeypatch):
    entity_id = "media_player.xiaomi_cn_636575596_lx04"
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["url"] = request.full_url
        return Response(json.dumps([[{
            "entity_id": entity_id,
            "state": "playing",
            "last_changed": "2026-09-23T08:00:01Z",
            "attributes": {"media_title": "example"},
        }]]).encode())

    monkeypatch.setattr(ha_companion, "urlopen", fake_urlopen)
    result = ha_companion.export_history("http://localhost:8123", "secret-token", {
        "entity_id": entity_id,
        "start": "2026-09-23T08:00:00+00:00",
        "end": "2026-09-23T08:01:00+00:00",
    })
    assert captured["authorization"] == "Bearer secret-token"
    assert "filter_entity_id=" in captured["url"]
    assert "secret-token" not in json.dumps(result)
    assert result["events"][0]["last_changed"].endswith("+00:00")

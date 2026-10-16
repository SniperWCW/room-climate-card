"""Static checks for the config flow that do not require Home Assistant."""

from __future__ import annotations

import json
from pathlib import Path
import re

PACKAGE_DIR = Path(__file__).parents[1] / "custom_components" / "room_climate"
SOURCE = (PACKAGE_DIR / "config_flow.py").read_text(encoding="utf-8")


def _shown_step_ids() -> set[str]:
    return set(re.findall(r'async_show_(?:form|menu)\(\s*step_id="([a-z_]+)"', SOURCE))


def test_forms_submit_back_to_their_own_step():
    """Home Assistant routes a submitted form to async_step_<step_id>.

    If a handler shows a form with another step_id, the input ends up in a
    different handler (this silently dropped manually added rooms).
    """
    mismatches = []
    for chunk in SOURCE.split("async def async_step_")[1:]:
        handler = chunk.split("(", 1)[0]
        for step_id in re.findall(r'async_show_(?:form|menu)\(\s*step_id="([a-z_]+)"', chunk):
            if step_id != handler:
                mismatches.append((handler, step_id))
    assert not mismatches, f"Handlers showing foreign step ids: {mismatches}"


def test_every_shown_step_is_translated():
    for path in sorted((PACKAGE_DIR / "translations").glob("*.json")):
        translations = json.loads(path.read_text(encoding="utf-8"))
        known = set(translations["config"]["step"]) | set(translations["options"]["step"])
        missing = _shown_step_ids() - known
        assert not missing, f"{path.name} lacks steps {missing}"


def test_optional_entities_use_suggested_values():
    """With a default, a cleared optional entity field would come back."""
    helper = SOURCE.split("def _optional_entity_field", 1)[1].split("\ndef ", 1)[0]
    assert "suggested_value" in helper
    assert "default=defaults[key]" not in helper

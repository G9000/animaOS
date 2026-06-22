from __future__ import annotations

import pytest

from anima_server.api.routes.config import get_persona_templates


@pytest.mark.asyncio
async def test_persona_template_descriptions_are_grounded() -> None:
    templates = await get_persona_templates()
    by_id = {template.id: template for template in templates}

    assert "neutral and practical" in by_id["default"].description.lower()
    assert "companion" not in by_id["default"].description.lower()

    combined = "\n".join(template.description for template in templates).lower()
    for phrase in (
        "deeply present",
        "meaningful connection",
        "understands deeply",
        "quiet and reflective",
    ):
        assert phrase not in combined

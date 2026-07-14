import asyncio

import pytest

from seo_ops.services.ai import AIUnavailable, build_ai_provider


def test_ai_is_optional(settings):
    provider = build_ai_provider(settings)
    with pytest.raises(AIUnavailable):
        asyncio.run(provider.complete_json("system", {"evidence": []}))

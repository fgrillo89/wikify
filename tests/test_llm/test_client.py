from unittest.mock import patch

from wikify.core.config import settings
from wikify.core.llm.client import complete, resolve_model_name


def test_resolve_model_name_maps_tier_aliases():
    original_model = settings.llm_model
    original_fast = settings.llm_fast_model
    original_deep = settings.llm_deep_model
    try:
        settings.llm_model = "balanced-model"
        settings.llm_fast_model = "fast-model"
        settings.llm_deep_model = "deep-model"

        assert resolve_model_name(None) == "balanced-model"
        assert resolve_model_name("balanced") == "balanced-model"
        assert resolve_model_name("fast") == "fast-model"
        assert resolve_model_name("cheap") == "fast-model"
        assert resolve_model_name("deep") == "deep-model"
        assert resolve_model_name("reasoning") == "deep-model"
        assert resolve_model_name("gpt-5-mini") == "gpt-5-mini"
    finally:
        settings.llm_model = original_model
        settings.llm_fast_model = original_fast
        settings.llm_deep_model = original_deep


def test_complete_resolves_alias_before_litellm_call():
    original_model = settings.llm_model
    original_fast = settings.llm_fast_model
    try:
        settings.llm_model = "balanced-model"
        settings.llm_fast_model = "fast-model"

        with patch("wikify.core.llm.client.litellm.completion") as mock_completion:
            mock_completion.return_value.choices = [
                type("Choice", (), {"message": type("Message", (), {"content": "ok"})()})()
            ]

            result = complete(
                messages=[{"role": "user", "content": "hello"}],
                model="fast",
                use_cache=False,
            )

        assert result == "ok"
        assert mock_completion.call_args.kwargs["model"] == "fast-model"
    finally:
        settings.llm_model = original_model
        settings.llm_fast_model = original_fast

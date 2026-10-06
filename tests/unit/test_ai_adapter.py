import pytest
from openai import OpenAIError
from pydantic import BaseModel

from backend.adapters.ai import OpenAIGateway
from backend.ports import AIProviderError
from tests.doubles import ScriptedAIClient

pytestmark = pytest.mark.unit


class _Schema(BaseModel):
    value: str


@pytest.mark.asyncio
async def test_provider_error_detail_is_not_leaked() -> None:
    gateway = OpenAIGateway(api_key="sk-test", default_model="gpt-4o")
    gateway._client = ScriptedAIClient(
        [OpenAIError("upstream detail sk-abcdefghijklmnop account=42")]
    )

    with pytest.raises(AIProviderError) as excinfo:
        await gateway.generate_structured(messages=[], response_format=_Schema)

    message = str(excinfo.value)
    assert message == "model provider request failed"
    assert "upstream detail" not in message
    assert "sk-" not in message

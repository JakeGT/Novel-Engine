"""Typed model context and capability negotiation (CTX-013): send, fall back with the
degradation recorded, or refuse before anything is sent."""

import base64
import types

import pytest
from pydantic import BaseModel

from backend.adapters.ai import OpenAIGateway, check_images, with_images
from backend.domain.context import ContextItem, ContextSection, assemble, stored_manifest
from backend.domain.context_parts import (
    MAX_IMAGE_TOKENS,
    MIN_IMAGE_TOKENS,
    UNKNOWN_IMAGE_TOKENS,
    CannotDeliver,
    ContextChoice,
    ImagePart,
    MediaLimits,
    Representation,
    TextPart,
    image_tokens,
    media_tokens,
    negotiate,
    planned_representation,
)
from backend.domain.errors import ValidationError
from backend.domain.media_observations import (
    MediaObservation,
    ObservationKind,
    ObservationState,
    Region,
    ReviewDecision,
    description_of,
    reviewed,
)
from backend.ports import AIUnsupportedInputError, ImageInput, ProviderCapabilities

pytestmark = pytest.mark.unit

SEES = MediaLimits(
    sees_images=True,
    max_images=2,
    max_image_bytes=1000,
    image_types=frozenset({"image/png", "image/webp"}),
)


def _picture(
    key: str = "picture:a", *, choice=ContextChoice.SEND, words="", size=500, kind="image/png"
):
    return ImagePart(
        key=key,
        asset_id=key.split(":")[-1],
        version_id=f"ver_{key[-1]}",
        content_type=kind,
        byte_length=size,
        checksum="c" * 64,
        label="“Mara”, version 1",
        fallback_text=words,
        choice=choice,
        width=1024,
        height=768,
    )


def test_a_picture_goes_to_a_route_that_can_see_it():
    delivery = negotiate([TextPart("t", "Some words."), _picture()], SEES)
    text, picture = delivery.parts
    assert text.representation is Representation.DESCRIPTION
    assert picture.representation is Representation.PICTURE and not picture.degraded
    assert picture.tokens == image_tokens(1024, 768) and delivery.sent() == [picture]
    assert delivery.degraded is False


@pytest.mark.parametrize(
    ("limits", "part", "why"),
    [
        (MediaLimits(), _picture(words="A grey coat."), "cannot see pictures"),
        (SEES, _picture(words="A grey coat.", size=5000), "larger than"),
        (SEES, _picture(words="A grey coat.", kind="image/jpeg"), "this kind of picture"),
    ],
)
def test_a_picture_that_cannot_go_falls_back_to_its_description_and_says_so(limits, part, why):
    (delivered,) = negotiate([part], limits).parts
    assert delivered.representation is Representation.FALLBACK and delivered.degraded
    assert why in delivered.reason and "description is given in words" in delivered.reason
    assert delivered.tokens == 4


def test_with_nothing_to_fall_back_on_it_is_only_named_and_that_is_a_degradation():
    (delivered,) = negotiate([_picture()], MediaLimits()).parts
    assert delivered.representation is Representation.NAMED and delivered.degraded


def test_a_route_takes_only_so_many_pictures():
    parts = [_picture(f"picture:{name}", words="Words.") for name in "abc"]
    delivery = negotiate(parts, SEES)
    assert [p.representation for p in delivery.parts] == [
        Representation.PICTURE,
        Representation.PICTURE,
        Representation.FALLBACK,
    ]
    assert "at most 2 pictures" in delivery.parts[2].reason


def test_the_authors_choice_is_kept_and_described_pictures_are_not_degraded():
    described = _picture(choice=ContextChoice.DESCRIBE, words="A grey coat.")
    left = _picture("picture:b", choice=ContextChoice.LEAVE_OUT, words="A grey coat.")
    first, second = negotiate([described, left], SEES).parts
    assert first.representation is Representation.DESCRIPTION and not first.degraded
    assert second.representation is Representation.LEFT_OUT and second.tokens == 0


def test_work_about_the_picture_itself_is_refused_before_anything_is_sent():
    with pytest.raises(CannotDeliver) as refused:
        negotiate(
            [_picture(choice=ContextChoice.DESCRIBE, words="x")],
            MediaLimits(),
            requires_original=True,
        )
    assert "nothing was sent" in str(refused.value) and refused.value.key == "picture:a"
    # Even a picture the author left out is sent when the work is about it.
    (only,) = negotiate(
        [_picture(choice=ContextChoice.LEAVE_OUT)], SEES, requires_original=True
    ).parts
    assert only.representation is Representation.PICTURE


def test_picture_costs_are_bounded_and_unknown_sizes_are_costed_high():
    assert image_tokens(None, None) == UNKNOWN_IMAGE_TOKENS
    assert image_tokens(10, 10) == MIN_IMAGE_TOKENS
    assert image_tokens(100_000, 100_000) == MAX_IMAGE_TOKENS
    parts = [
        _picture(words="One two three."),
        _picture("picture:b", choice=ContextChoice.DESCRIBE, words="One two."),
    ]
    assert media_tokens(parts) == image_tokens(1024, 768) + 3
    assert media_tokens(parts, MediaLimits()) == 4 + 3
    assert planned_representation(ContextChoice.DESCRIBE, False) is Representation.NAMED


def test_a_picture_to_send_costs_its_tokens_against_the_budget_and_is_kept_by_version():
    item = ContextItem(
        key="picture:a",
        section=ContextSection.PICTURES,
        text="A picture of Mara.",
        reason="It shows Mara.",
        authority="picture",
        record_type="asset",
        record_id="a",
        media=_picture(),
    )
    assert item.words == round(image_tokens(1024, 768) * 3 / 4 + 0.5)
    manifest = assemble([item], pins=["picture:a"])
    kept = stored_manifest(manifest, None, negotiate([item.media], MediaLimits()))
    (record,) = kept["items"]
    assert record["picture"]["version_id"] == "ver_a" and record["picture"]["choice"] == "send"
    assert kept["delivery"]["degraded"] is True
    assert "data" not in str(kept)


def _image(size: int = 10, kind: str = "image/png") -> ImageInput:
    return ImageInput(data=b"\x89PNG" + b"0" * size, content_type=kind, ref="picture 1")


def test_pictures_go_after_the_last_users_words_as_inline_addresses():
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "look"},
    ]
    shaped = with_images(messages, [_image()])
    assert shaped[:3] == messages[:3]
    text, picture = shaped[3]["content"]
    assert text == {"type": "text", "text": "look"}
    url = picture["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == _image().data
    assert with_images(messages, []) == messages


def test_the_gateway_checks_the_routes_declared_limits():
    text_only = ProviderCapabilities(provider="p", model="m")
    with pytest.raises(AIUnsupportedInputError):
        check_images([_image()], text_only)
    sees = ProviderCapabilities(
        provider="p",
        model="m",
        input_modalities=frozenset({"text", "image"}),
        max_images=1,
        max_image_bytes=100,
        image_types=frozenset({"image/png"}),
    )
    check_images([_image()], sees)
    for images in ([_image(), _image()], [_image(500)], [_image(kind="image/gif")]):
        with pytest.raises(AIUnsupportedInputError):
            check_images(images, sees)


class _Answer(BaseModel):
    value: str


@pytest.mark.asyncio
async def test_a_text_only_gateway_refuses_before_building_a_client(monkeypatch):
    gateway = OpenAIGateway(api_key="sk-test", default_model="m")
    built: list = []
    monkeypatch.setattr(gateway, "_build_client", lambda *args: built.append(args))
    with pytest.raises(AIUnsupportedInputError):
        await gateway.generate_structured(
            messages=[{"role": "user", "content": "hi"}],
            response_format=_Answer,
            api_key="sk-byok",
            base_url="https://openrouter.ai/api/v1",
            images=[_image()],
        )
    with pytest.raises(AIUnsupportedInputError):
        await gateway.generate_text(messages=[], max_tokens=10, api_key="k", images=[_image()])
    assert built == []


@pytest.mark.asyncio
async def test_a_gateway_declared_to_see_pictures_sends_them(monkeypatch):
    holder: dict = {}

    async def _parse(**kwargs):
        holder["parse"] = kwargs
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(message=types.SimpleNamespace(parsed=_Answer(value="ok")))
            ],
            usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )

    client = types.SimpleNamespace(
        beta=types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(parse=_parse))
        )
    )
    gateway = OpenAIGateway(api_key="sk-test", default_model="m", image_input=True)
    gateway._client = client
    assert gateway.capabilities().sees_images
    response = await gateway.generate_structured(
        messages=[{"role": "user", "content": "look"}], response_format=_Answer, images=[_image()]
    )
    assert response.parsed.value == "ok"
    content = holder["parse"]["messages"][0]["content"]
    assert content[1]["type"] == "image_url"


def test_a_workspaces_own_provider_sees_pictures_only_when_the_operator_says_so():
    gateway = OpenAIGateway(api_key="", default_model="m", image_input=True)
    assert not gateway.capabilities(base_url="https://openrouter.ai/api/v1").sees_images
    listed = OpenAIGateway(api_key="", default_model="m", image_providers=frozenset({"openrouter"}))
    assert listed.capabilities(base_url="https://openrouter.ai/api/v1").sees_images
    assert not listed.capabilities().sees_images


def _observation(**fields) -> MediaObservation:
    return MediaObservation(
        workspace_id="w",
        project_id="p",
        id=fields.pop("id", "obs_1"),
        asset_id="ast_1",
        asset_version_id=fields.pop("version", "ver_1"),
        kind=fields.pop("kind", ObservationKind.APPEARANCE),
        text=fields.pop("text", "A grey coat."),
        **fields,
    )


def test_review_states_and_the_words_that_stand():
    proposed = _observation()
    accepted = reviewed(proposed, ReviewDecision.ACCEPT, corrected_text=None, actor_user_id="u")
    assert accepted.state is ObservationState.ACCEPTED and accepted.reviewed_by == "u"
    corrected = reviewed(
        accepted, ReviewDecision.CORRECT, corrected_text=" A  green coat ", actor_user_id="u"
    )
    assert corrected.words == "A green coat"
    rejected = reviewed(proposed, ReviewDecision.REJECT, corrected_text=None, actor_user_id="u")
    with pytest.raises(ValidationError):
        reviewed(rejected, ReviewDecision.ACCEPT, corrected_text=None, actor_user_id="u")
    offered = accepted.model_copy(update={"proposal_mutation_set_id": "mut_1"})
    with pytest.raises(ValidationError):
        reviewed(offered, ReviewDecision.REJECT, corrected_text=None, actor_user_id="u")
    caption = _observation(
        id="obs_2", kind=ObservationKind.CAPTION, text="A quay", state=ObservationState.ACCEPTED
    )
    other = _observation(id="obs_3", version="ver_2", state=ObservationState.ACCEPTED)
    assert description_of([corrected, caption, other, proposed], "ver_1") == "A quay. A green coat."


def test_a_region_must_lie_on_the_picture():
    assert (
        Region(x=0.1, y=0.2, width=0.3, height=0.4).location() == "region:0.100,0.200,0.300,0.400"
    )
    with pytest.raises(ValueError):
        Region(x=0.8, y=0, width=0.5, height=0.1)

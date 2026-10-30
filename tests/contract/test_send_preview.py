"""What will be sent, and where, before it is sent (CTX-013): asked first whenever pictures
go to a company's servers, and shown with every question about size."""

from __future__ import annotations

import pytest

from backend.domain.context_parts import ImagePart, MediaLimits
from backend.domain.usage_controls import NeedsConfirmation
from backend.services.send_preview import (
    COMPANY,
    LOCAL_NETWORK,
    YOUR_COMPUTER,
    SendPreview,
    ask_before_sending,
    classify,
)
from tests.contract.test_picture_context import _choose, _run, _scene_with_picture

pytestmark = pytest.mark.contract


def test_pictures_to_a_company_are_asked_about_first_saying_what_and_where(
    auth_client, fake_container
):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "send")
    fake_container.ai.sees_images = True
    url = f"{w['base']}/plan/{scene['id']}/studio/drafts"

    estimate = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/estimate", json={"variants": 1, "words": 200}
    ).json()
    sending = estimate["sending"]
    assert sending["provider"] == "openai" and sending["destination"] == "api.openai.com"
    assert sending["where"] == "company"
    assert sending["where_words"] == "to OpenAI’s servers, outside this site"
    assert sending["pictures"] == [
        {
            "asset_id": asset_id,
            "version_id": sending["pictures"][0]["version_id"],
            "name": "“Mara on the quay”, version 1",
        }
    ]
    assert sending["words"] == estimate["context_words"] > 0
    assert sending["estimated_tokens"] == estimate["total_tokens"]

    asked = auth_client.post(url, json={"variants": 1, "words": 200})
    assert asked.status_code == 409, asked.text
    body = asked.json()
    assert body["code"] == "needs_confirmation"
    assert body["detail"] == (
        "Writing this draft sends a picture from your story to OpenAI’s servers, outside "
        f"this site, with about {estimate['total_tokens']:,} tokens of words."
    )
    assert {k: v for k, v in body["sending"].items() if k != "digest"} == sending
    assert len(body["sending"]["digest"]) == 32
    assert fake_container.ai.calls == []

    # The size alone is not the answer: the answer names what the author was shown.
    tokens_only = auth_client.post(
        url,
        json={"variants": 1, "words": 200},
        headers={"X-Confirm-Tokens": body["field_errors"]["estimated_tokens"]},
    )
    assert tokens_only.status_code == 409, tokens_only.text
    other = auth_client.post(
        url,
        json={"variants": 1, "words": 200},
        headers={
            "X-Confirm-Tokens": body["field_errors"]["estimated_tokens"],
            "X-Confirm-Sending": "0" * 32,
        },
    )
    assert other.status_code == 409, other.text
    assert fake_container.ai.calls == []

    fake_container.ai.queue("Tobin waited. Mara came down the quay in her blue coat.")
    agreed = auth_client.post(
        url,
        json={"variants": 1, "words": 200},
        headers={
            "X-Confirm-Tokens": body["field_errors"]["estimated_tokens"],
            "X-Confirm-Sending": body["sending"]["digest"],
        },
    )
    assert agreed.status_code == 202, agreed.text
    (job,) = _run(fake_container)
    assert job.status.value == "succeeded"
    assert len(fake_container.ai.calls[-1]["images"]) == 1


def test_a_described_picture_goes_in_words_and_is_not_asked_about(auth_client, fake_container):
    w, scene, _asset_id = _scene_with_picture(auth_client, fake_container)
    fake_container.ai.sees_images = True
    estimate = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/estimate", json={"variants": 1, "words": 200}
    ).json()
    assert estimate["sending"]["pictures"] == []
    fake_container.ai.queue("Tobin waited.")
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/drafts", json={"variants": 1, "words": 200}
    )
    assert started.status_code == 202, started.text


def test_a_question_about_size_says_what_and_where_too(auth_client, fake_container):
    w, scene, _asset_id = _scene_with_picture(auth_client, fake_container)
    estimate = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/estimate", json={"variants": 3, "words": 2000}
    ).json()
    assert estimate["total_tokens"] > 1_000
    put = auth_client.put(f"{w['base']}/assistant-settings", json={"confirm_above_tokens": 1_000})
    assert put.status_code == 200, put.text
    asked = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/drafts", json={"variants": 3, "words": 2000}
    )
    assert asked.status_code == 409, asked.text
    body = asked.json()
    assert "more than the" in body["detail"]
    assert body["sending"]["provider_label"] == "OpenAI" and body["sending"]["pictures"] == []


# ------------------------------------------------------------------- the rules


def _preview(where: str, pictures: int = 1) -> SendPreview:
    from backend.services.send_preview import PictureSent

    return SendPreview(
        provider="openrouter",
        provider_label="OpenRouter",
        model="m",
        destination="openrouter.ai",
        where=where,
        words=100,
        estimated_tokens=500,
        pictures=[PictureSent("ast", "ver", "A picture")] * pictures,
    )


def test_where_a_request_goes_is_said_by_its_address():
    assert classify("https://openrouter.ai/api/v1") == (COMPANY, "openrouter.ai")
    assert classify("http://localhost:11434/v1") == (LOCAL_NETWORK, "localhost")
    assert classify("http://127.0.0.1:1234/v1") == (LOCAL_NETWORK, "127.0.0.1")
    assert classify("http://192.168.1.20:8080/v1") == (LOCAL_NETWORK, "192.168.1.20")
    assert classify("http://ollama:11434/v1") == (LOCAL_NETWORK, "ollama")
    assert classify("http://gpu.lan/v1") == (LOCAL_NETWORK, "gpu.lan")
    assert classify("https://8.8.8.8/v1") == (COMPANY, "8.8.8.8")


def test_only_pictures_to_a_company_are_asked_about_and_a_matching_answer_lets_them_go():
    for where in (LOCAL_NETWORK, YOUR_COMPUTER):
        ask_before_sending(
            _preview(where), estimated_tokens=500, confirmed_tokens=None, threshold=None, doing="X"
        )
    ask_before_sending(
        _preview(COMPANY, pictures=0),
        estimated_tokens=500,
        confirmed_tokens=None,
        threshold=None,
        doing="X",
    )
    with pytest.raises(NeedsConfirmation) as asked:
        ask_before_sending(
            _preview(COMPANY, 2),
            estimated_tokens=500,
            confirmed_tokens=None,
            threshold=None,
            doing="Checking this scene",
        )
    assert asked.value.detail.startswith("Checking this scene sends 2 pictures from your story")
    assert (
        asked.value.extensions["sending"]["where_words"]
        == "to OpenRouter’s servers, outside this site"
    )
    # An answer to another question (a much smaller or larger size) is not this one.
    with pytest.raises(NeedsConfirmation):
        ask_before_sending(
            _preview(COMPANY), estimated_tokens=500, confirmed_tokens=10, threshold=None, doing="X"
        )
    # The size without the digest of what was shown, or with another's, is not it either.
    with pytest.raises(NeedsConfirmation):
        ask_before_sending(
            _preview(COMPANY), estimated_tokens=500, confirmed_tokens=500, threshold=None, doing="X"
        )
    with pytest.raises(NeedsConfirmation):
        ask_before_sending(
            _preview(COMPANY),
            estimated_tokens=500,
            confirmed_tokens=500,
            threshold=None,
            doing="X",
            confirmed_digest=_preview(COMPANY, 2).digest + "x",
        )
    ask_before_sending(
        _preview(COMPANY),
        estimated_tokens=500,
        confirmed_tokens=500,
        threshold=None,
        doing="X",
        confirmed_digest=_preview(COMPANY).digest,
    )


def test_the_preview_lists_only_what_the_route_takes_as_pictures():
    import asyncio

    from backend.domain.context_parts import ContextChoice

    class _Settings:
        is_hosted = False
        AI_BASE_URL = "https://openrouter.ai/api/v1"
        AI_PROVIDER = "openrouter"
        AI_MODEL = "some/model"

    from backend.services.send_preview import SendPreviewService

    def part(key: str, choice: ContextChoice) -> ImagePart:
        return ImagePart(
            key=key,
            asset_id=f"ast_{key}",
            version_id=f"ver_{key}",
            content_type="image/webp",
            byte_length=100,
            checksum="c",
            label=f"Picture {key}",
            fallback_text="words",
            choice=choice,
        )

    service = SendPreviewService(settings=_Settings())
    limits = MediaLimits(
        sees_images=True, max_images=1, max_image_bytes=1000, image_types=frozenset({"image/webp"})
    )
    found = asyncio.run(
        service.preview(
            None,  # type: ignore[arg-type]
            "w",
            words=10,
            estimated_tokens=50,
            media=[
                part("a", ContextChoice.SEND),
                part("b", ContextChoice.SEND),
                part("c", ContextChoice.DESCRIBE),
            ],
            limits=limits,
        )
    )
    assert found is not None
    # The route takes one picture: the second goes as its description, the third was
    # always to be described.
    assert [p.name for p in found.pictures] == ["Picture a"]
    assert found.provider_label == "OpenRouter" and found.where == COMPANY


# ------------------------------------------------- the model the workspace chose


def _send_part(key: str = "a") -> ImagePart:
    from backend.domain.context_parts import ContextChoice

    return ImagePart(
        key=f"picture:ast_{key}",
        asset_id=f"ast_{key}",
        version_id=f"ver_{key}",
        content_type="image/webp",
        byte_length=100,
        checksum="c",
        label=f"Picture {key}",
        fallback_text="words",
        choice=ContextChoice.SEND,
    )


def _chosen_route(*, fetch=None):
    """A hosted site whose operator's own model (``AI_MODEL``) is text only, and a
    workspace that chose OpenRouter's ``openai/gpt-4o``, which sees pictures."""
    import time
    from types import SimpleNamespace

    from backend.adapters.ai import OpenAIGateway
    from backend.domain.model_capabilities import ModelCapability
    from backend.services.media_observations import MediaObservationService
    from backend.services.model_capabilities import ModelCapabilityService
    from backend.services.send_preview import SendPreviewService

    settings = SimpleNamespace(
        is_hosted=True,
        AI_BASE_URL="",
        AI_PROVIDER="openai",
        AI_MODEL="deepseek/deepseek-chat",
    )

    class _Credentials:
        async def ai_choice(self, scope, workspace_id):
            return SimpleNamespace(provider="openrouter", model="openai/gpt-4o", chosen=True)

    gateway = OpenAIGateway(
        "",
        default_model="deepseek/deepseek-chat",
        provider="openai",
        base_url="",
        image_input=False,
        image_providers=frozenset({"openrouter"}),
    )
    models = ModelCapabilityService(fetch=fetch, read_lists=fetch is not None)
    if fetch is None:
        models._remember(
            "openrouter",
            [
                ModelCapability(
                    "openrouter", "deepseek/deepseek-chat", sees_images=False, source="provider"
                ),
                ModelCapability(
                    "openrouter",
                    "openai/gpt-4o",
                    sees_images=True,
                    context_tokens=128_000,
                    source="provider",
                ),
            ],
            time.time(),
        )
    gateway.attach_models(models)
    credentials = _Credentials()
    pictures = MediaObservationService(
        store=None,
        media=None,
        reader=None,
        mutation=None,
        journal=None,
        runner=None,
        ai=gateway,
        settings=settings,
        credentials=credentials,
    )
    previews = SendPreviewService(settings=settings, credentials=credentials, ai=gateway)
    return pictures, previews


def test_the_preview_and_the_limits_follow_the_model_the_workspace_chose():
    """The operator's own model is text only; the workspace chose one that sees pictures.
    The preview must show the picture going (and so ask first): before, the limits and the
    preview read the operator's model and the picture went without asking."""
    import asyncio

    pictures, previews = _chosen_route()

    async def go():
        limits = await pictures.route_limits(None, "w")  # type: ignore[arg-type]
        return limits, await previews.preview(
            None,  # type: ignore[arg-type]
            "w",
            words=10,
            estimated_tokens=50,
            media=[_send_part()],
            limits=limits,
        )

    limits, found = asyncio.run(go())
    assert limits.sees_images
    assert found is not None and found.model == "openai/gpt-4o"
    assert [p.name for p in found.pictures] == ["Picture a"]
    # What the chosen model reads at most, not the operator's model.
    assert found.context_tokens == 128_000
    with pytest.raises(NeedsConfirmation) as asked:
        ask_before_sending(
            found, estimated_tokens=50, confirmed_tokens=None, threshold=None, doing="X"
        )
    assert asked.value.extensions["sending"]["pictures"][0]["version_id"] == "ver_a"


def test_a_fresh_worker_reads_the_list_before_its_limits_and_gives_descriptions():
    """A worker that has not read the provider's list yet: before, its limits were worked
    out from nothing known (pictures allowed) and the picture was then refused by the
    gateway, failing the job. Now the list is read first and the picture is described."""
    import asyncio
    import json

    from backend.domain.context_parts import negotiate
    from backend.domain.providers import set_request_model

    asked: list[str] = []

    async def fetch(url: str) -> bytes:
        asked.append(url)
        listed = {"id": "words/only", "architecture": {"input_modalities": ["text"]}}
        return json.dumps({"data": [listed]}).encode()

    pictures, _ = _chosen_route(fetch=fetch)
    try:
        set_request_model("words/only")
        limits = asyncio.run(pictures.limits_for("https://openrouter.ai/api/v1"))
    finally:
        set_request_model(None)
    assert len(asked) == 1
    assert not limits.sees_images
    (part,) = negotiate([_send_part()], limits).parts
    # Given in words (its description) rather than as the picture.
    assert part.representation.value == "fallback"


def test_when_where_pictures_go_cannot_be_said_the_author_is_asked():
    """``sending`` used to swallow a failed preview and return nothing, so pictures went
    without asking; now it is taken as a company's servers, and asked about."""
    import asyncio
    from types import SimpleNamespace

    from backend.domain.errors import ConflictError
    from backend.services.studio_service import Estimate, StudioService

    class _Broken:
        async def preview(self, *args, **kwargs):
            raise ConflictError("not now")

    studio = SimpleNamespace(previews=_Broken(), _context=SimpleNamespace(pictures=None))
    estimate = Estimate(
        prompt_tokens=40, completion_tokens=10, context_words=30, pictures=(_send_part(),)
    )
    found = asyncio.run(StudioService.sending(studio, None, "w", estimate))  # type: ignore[arg-type]
    assert found is not None and found.asks_first
    assert [p.version_id for p in found.pictures] == ["ver_a"]
    with pytest.raises(NeedsConfirmation):
        ask_before_sending(
            found, estimated_tokens=50, confirmed_tokens=None, threshold=None, doing="X"
        )
    # Nothing to send as a picture: nothing to ask.
    words_only = Estimate(prompt_tokens=40, completion_tokens=10, context_words=30)
    assert asyncio.run(StudioService.sending(studio, None, "w", words_only)) is None  # type: ignore[arg-type]


def test_a_job_stops_if_it_would_send_pictures_it_was_not_agreed_to_send():
    from backend.services.send_preview import agreed_sending, ask_again_before_sending

    agreed = {"confirmed_sending": agreed_sending(_preview(COMPANY))}
    # The same pictures, the same way: it goes on.
    ask_again_before_sending(agreed, _preview(COMPANY), doing="X")
    # Asked for before this was kept: not stopped for it.
    ask_again_before_sending({}, _preview(COMPANY), doing="X")
    # Another picture, or another model, or nothing agreed: it stops.
    other = SendPreview(**{**_preview(COMPANY).__dict__, "model": "another"})
    with pytest.raises(NeedsConfirmation) as stopped:
        ask_again_before_sending(agreed, other, doing="Writing this draft")
    assert stopped.value.detail.startswith("Writing this draft stopped")
    from backend.services.send_preview import PictureSent

    more = SendPreview(
        **{
            **_preview(COMPANY).__dict__,
            "pictures": [PictureSent("ast", "ver", "A"), PictureSent("ast2", "ver2", "B")],
        }
    )
    with pytest.raises(NeedsConfirmation):
        ask_again_before_sending(agreed, more, doing="X")
    with pytest.raises(NeedsConfirmation):
        ask_again_before_sending({"confirmed_sending": None}, _preview(COMPANY), doing="X")
    # To the site's own network, nothing needs agreeing.
    ask_again_before_sending({"confirmed_sending": None}, _preview(LOCAL_NETWORK), doing="X")


# ------------------------------------------- work the author did not ask for just now


def _written_scene_with_picture_to_send(auth_client, fake_container):
    from tests.contract.test_picture_context import PROSE

    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "send")
    fake_container.ai.sees_images = True
    saved = auth_client.put(
        f"{w['base']}/plan/{scene['id']}/studio/text",
        json={"content": PROSE, "base_version_id": None},
    )
    assert saved.status_code == 200, saved.text
    return w, scene, asset_id, saved.json()


def test_an_automatic_check_leaves_out_pictures_that_would_go_to_a_company(
    auth_client, fake_container
):
    """Nobody is there to agree, so the pictures pass is left out of a check started on the
    author's behalf; before, it ran and sent the picture to OpenAI without asking."""
    import asyncio

    from tests.contract.test_picture_context import PROSE

    w, scene, _asset_id, first = _written_scene_with_picture_to_send(auth_client, fake_container)
    put = auth_client.put(
        f"{w['base']}/assistant-settings", json={"check_profile": "thorough", "check_minutes": 5}
    )
    assert put.status_code == 200, put.text
    saved = auth_client.put(
        f"{w['base']}/plan/{scene['id']}/studio/text",
        json={"content": PROSE + " Gulls cried.", "base_version_id": first["version_id"]},
    ).json()
    assert saved["automatic"] == "started", saved
    started = next(job for job in saved["jobs"] if job["automatic"])
    stored = asyncio.run(
        fake_container.jobs.get(
            workspace_id=str(fake_container.settings.AUTH_LOCAL_WORKSPACE_ID),
            project_id=w["base"].rsplit("/", 1)[1],
            job_id=started["id"],
        )
    )
    assert stored is not None
    assert "pictures" not in stored.payload["passes"]
    assert stored.payload["confirmed_sending"] is None
    for _ in range(len(stored.payload["passes"])):
        fake_container.ai.queue(_no_notes())
    _run(fake_container)
    assert all(not call.get("images") for call in fake_container.ai.calls)


def _no_notes():
    from backend.ports import AIUsage, StructuredResponse
    from backend.services.scene_checks import CheckNotes

    return StructuredResponse(
        parsed=CheckNotes(notes=[]),
        model="fake",
        usage=AIUsage(prompt_tokens=10, completion_tokens=0),
    )


def test_an_audit_with_pictures_asks_first_and_goes_once_agreed(auth_client, fake_container):
    """A deep audit's pictures pass sent the picture to a company without asking."""
    from backend.ports import AIUsage, StructuredResponse
    from backend.services.scene_checks import PictureNotes

    w, scene, asset_id, _ = _written_scene_with_picture_to_send(auth_client, fake_container)
    body = {"plan_id": scene["id"], "passes": ["pictures"]}
    asked = auth_client.post(f"{w['base']}/audits", json=body)
    assert asked.status_code == 409, asked.text
    question = asked.json()
    assert question["detail"].startswith("Auditing this part of the story sends a picture")
    assert [p["asset_id"] for p in question["sending"]["pictures"]] == [asset_id]
    assert fake_container.ai.calls == []

    tokens_only = auth_client.post(
        f"{w['base']}/audits",
        json=body,
        headers={"X-Confirm-Tokens": question["field_errors"]["estimated_tokens"]},
    )
    assert tokens_only.status_code == 409, tokens_only.text
    agreed = auth_client.post(
        f"{w['base']}/audits",
        json=body,
        headers={
            "X-Confirm-Tokens": question["field_errors"]["estimated_tokens"],
            "X-Confirm-Sending": question["sending"]["digest"],
        },
    )
    assert agreed.status_code == 202, agreed.text
    fake_container.ai.queue(
        StructuredResponse(
            parsed=PictureNotes(notes=[]),
            model="fake",
            usage=AIUsage(prompt_tokens=10, completion_tokens=0),
        )
    )
    (job,) = _run(fake_container)
    assert job.status.value == "succeeded", job.error_message
    assert len(fake_container.ai.calls[-1]["images"]) == 1

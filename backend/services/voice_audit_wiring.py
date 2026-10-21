"""Putting the voice, audit schedules, correction plans, what-if comparisons and finding
proposals together (CON-018, CON-011, CON-010), for the app's container and the tests'.

Every piece is built on the studio's own services; this only connects them, so the app
and the tests wire them the same way.
"""

from __future__ import annotations

from typing import Any


def wire_voice_and_audits(
    container: Any, *, baselines: Any, schedules: Any, corrections: Any, model: str | None = None
) -> None:
    from backend.services.audit_schedules import AuditScheduleService
    from backend.services.correction_plans import CorrectionPlanService
    from backend.services.finding_proposals import FindingProposalService
    from backend.services.voice_service import VoiceService
    from backend.services.what_if_audit import WhatIfAudit

    studio = container.studio_service
    audits = studio.audits
    container.voice_service = VoiceService(studio=studio, store=baselines)
    audits.voice = container.voice_service
    container.audit_schedules = AuditScheduleService(audits=audits, store=schedules)
    container.audit_schedules.register(container.job_runner)
    container.correction_plans = CorrectionPlanService(
        audits=audits,
        store=corrections,
        ai=container.ai,
        settings=container.settings,
        credentials=container.credential_service,
        usage=container.usage_service,
        model=model,
    )
    container.correction_plans.register_jobs(container.job_runner)
    container.what_if_audit = WhatIfAudit(studio=studio, voice=container.voice_service)
    container.finding_proposals = FindingProposalService(
        studio=studio,
        workflow=container.canon_character_workflow,
        settings=container.settings,
        credentials=container.credential_service,
        usage=container.usage_service,
    )
    if container.archive_service is not None:
        container.archive_service.voice = container.voice_service


__all__ = ["wire_voice_and_audits"]

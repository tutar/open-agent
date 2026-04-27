"""Verifier task helpers for the local harness baseline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from openagent.harness.task.background import LocalBackgroundAgentOrchestrator
from openagent.harness.task.models import (
    BackgroundTaskContext,
    VerificationRequest,
    VerificationResult,
    VerificationVerdict,
    VerifierTaskHandle,
)
from openagent.object_model import JsonObject, JsonValue
from openagent.tools import ReviewCommandKind, ReviewContext, ReviewResult, StaticCommandRegistry


@dataclass(slots=True)
class LocalVerificationRuntime:
    """Run review/verification commands through verifier tasks."""

    orchestrator: LocalBackgroundAgentOrchestrator

    def spawn_verifier(self, request: VerificationRequest) -> VerifierTaskHandle:
        return self.orchestrator.start_verifier_task(
            request.prompt,
            lambda context: self._run_default_verifier(context, request),
            metadata=cast(JsonObject, {
                "target_session": request.target_session,
                "original_task": request.original_task,
                "changed_artifacts": cast(list[JsonValue], list(request.changed_artifacts)),
                "evidence_scope": cast(list[JsonValue], list(request.evidence_scope)),
                "review_policy": request.review_policy,
                "source_command_id": request.source_command_id,
            }),
            session_id=request.target_session,
        )

    def await_verifier(
        self,
        handle: VerifierTaskHandle,
        timeout: float | None = None,
    ) -> VerificationResult:
        return self.orchestrator._task_manager.await_verifier(handle, timeout)

    def cancel_verifier(self, handle: VerifierTaskHandle) -> None:
        self.orchestrator.kill_task(handle.task_id)

    def attach_verification(self, target: str, result: VerificationResult) -> ReviewResult:
        return ReviewResult(
            kind=ReviewCommandKind.VERIFICATION,
            verdict=result.verdict.value,
            evidence=result.evidence + [f"attached_to:{target}"],
            findings=result.findings,
            limitations=result.limitations,
            output_ref=result.output_ref,
        )

    def register_verification_command(
        self,
        registry: StaticCommandRegistry,
        command_id: str = "cmd.review",
    ) -> None:
        command = registry.resolve_command(command_id)

        def _handler(args: JsonObject) -> JsonValue:
            target_session = str(args.get("target_session", "review"))
            original_task = str(args.get("original_task", "verification"))
            prompt = str(args.get("prompt", command.description))
            changed_artifacts_raw = args.get("changed_artifacts")
            evidence_scope_raw = args.get("evidence_scope")
            review_policy_raw = args.get("review_policy")
            handle = self.spawn_verifier(
                VerificationRequest(
                    target_session=target_session,
                    original_task=original_task,
                    prompt=prompt,
                    changed_artifacts=[
                        str(item) for item in changed_artifacts_raw
                    ]
                    if isinstance(changed_artifacts_raw, list)
                    else [],
                    evidence_scope=[
                        str(item) for item in evidence_scope_raw
                    ]
                    if isinstance(evidence_scope_raw, list)
                    else [],
                    review_policy=dict(review_policy_raw)
                    if isinstance(review_policy_raw, dict)
                    else {},
                    source_command_id=command.id,
                )
            )
            result = self.await_verifier(handle)
            return self.attach_verification(target_session, result).to_dict()

        registry.register(command, _handler)

    def _run_default_verifier(
        self,
        context: BackgroundTaskContext,
        request: VerificationRequest,
    ) -> VerificationResult:
        context.progress({"summary": "collecting evidence"})
        context.checkpoint({"step": "verification_started"})
        evidence = [
            f"target_session:{request.target_session}",
            f"original_task:{request.original_task}",
        ]
        if request.changed_artifacts:
            evidence.extend(f"artifact:{item}" for item in request.changed_artifacts)
        output_ref = f"memory://tasks/{context.task_id}/verification"
        context.output(
            cast(JsonObject, {
                "summary": request.prompt,
                "evidence": cast(list[JsonValue], list(evidence)),
            })
        )
        return VerificationResult(
            verdict=VerificationVerdict.PARTIAL,
            summary=request.prompt,
            evidence=evidence,
            findings=["Verification baseline requires explicit evidence review."],
            limitations=["Default local verifier is deterministic baseline only."],
            task_id=context.task_id,
            output_ref=output_ref,
        )


def build_review_context(session_id: str, original_task: str) -> ReviewContext:
    return ReviewContext(target_session=session_id, original_task=original_task)

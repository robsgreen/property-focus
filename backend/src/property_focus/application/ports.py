"""Ports: the protocols application code uses to reach infrastructure."""

from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict


class SecretNotFoundError(LookupError):
    """No value is configured for a logical secret name.

    The message names the logical name only, never a value.
    """


class SecretProvider(Protocol):
    """Resolves secrets and configuration values by logical name.

    A logical name is lowercase snake case (``openai_api_key``) and means the same thing in
    every environment; where the value is stored is the provider's concern.
    """

    async def get(self, logical_name: str) -> str:
        """Return the value for ``logical_name``.

        Raises ``SecretNotFoundError`` when no value is configured, and ``ValueError`` when
        ``logical_name`` is not lowercase snake case.
        """
        ...


# The TaskInput shape a consumer must understand; bump it on an incompatible change.
TASK_INPUT_SCHEMA_VERSION = 2


class TaskInput(BaseModel):
    """The envelope that starts one task for one admitted workflow run.

    It carries IDs and digests only and travels through queues, process arguments and
    invocation payloads, so it is untrusted: the task loads the tenant and scope from the
    workflow run it names, never from these fields, and refuses an envelope whose kind or
    input digest disagrees with that run. Unknown fields are rejected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = TASK_INPUT_SCHEMA_VERSION
    workflow_run_id: UUID
    task_kind: str
    # The aggregate the task works on, such as a property or an ingested message.
    subject_type: str
    subject_id: UUID
    input_digest: str
    # Version of the application package that built the envelope.
    application_version: str
    # Stable for every delivery of one launch, so an execution name or a provider
    # idempotency key derived from it repeats on duplicate delivery; each relaunch of the
    # run gets a new one.
    idempotency_key: str
    # Automatic retries stop at this time.
    deadline: AwareDatetime


class TaskLauncher(Protocol):
    """Starts a task in its own short-lived execution (process, Lambda, state machine or
    container) without waiting for it to finish.

    Delivery is at least once: the same envelope may start more than one execution, and
    the task deduplicates by its workflow run.
    """

    async def launch(self, task: TaskInput) -> str:
        """Start ``task`` and return a reference to the execution, for logs and operators.

        Raises ``CodedError`` (``rate_limited`` or ``dependency_transient``) when the
        execution service refuses the start for a reason worth retrying later.
        """
        ...

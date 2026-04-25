import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# The proposal columns are sql TEXT (unbounded). We still need an
# upstream cap: otherwise an attacker can bloat rows with megabytes of
# free text, and worse — the EditArtifact anti-hallucination check does
# a 5-word window scan across proposal_text, which is O(N*M) on the
# comment text. 10k chars is roomy for a detailed rationale while
# making multi-MB abuse a non-starter.
_TEXT_MAX = 10_000


class ProposalCreate(BaseModel):
    user_id: uuid.UUID
    proposal_type: str
    proposal_text: str = Field(default="", max_length=_TEXT_MAX)
    # `pitch` is the proposer's rationale — why this should be accepted.
    # Optional on the wire for legacy tooling, but the UI and bots are
    # expected to populate it. Stored in its own column.
    pitch: str | None = Field(default=None, max_length=_TEXT_MAX)
    val_uuid: uuid.UUID | None = None
    val_text: str = Field(default="", max_length=_TEXT_MAX)


class ProposalResponse(BaseModel):
    id: uuid.UUID
    community_id: uuid.UUID
    user_id: uuid.UUID
    proposal_type: str
    proposal_status: str
    proposal_text: str
    pitch: str | None = None
    val_uuid: uuid.UUID | None
    val_text: str | None
    pulse_id: uuid.UUID | None
    age: int
    support_count: int
    created_at: datetime
    prev_content: str | None = None
    # Amendment chain. When a proposal is amended, the new row's
    # `parent_proposal_id` points back at the predecessor and
    # `version` is incremented. Top-level rows have parent=None and
    # version=1.
    parent_proposal_id: uuid.UUID | None = None
    version: int = 1
    # Computed enrichment fields. `promote_threshold` is the member
    # count needed to move OutThere → OnTheAir (ProposalSupport %).
    # `decide_threshold` is the per-type threshold for execution
    # when OnTheAir (e.g. Funding %, Membership %, etc). Both are
    # None only for brand-new proposals fetched before enrichment.
    promote_threshold: int | None = None
    decide_threshold: int | None = None
    user_name: str | None = None
    display_name: str | None = None

    model_config = {"from_attributes": True}


class ProposalEdit(BaseModel):
    user_id: uuid.UUID
    proposal_text: str | None = Field(default=None, max_length=_TEXT_MAX)
    pitch: str | None = Field(default=None, max_length=_TEXT_MAX)
    val_text: str | None = Field(default=None, max_length=_TEXT_MAX)


class ProposalAmend(BaseModel):
    """Body for POST /proposals/{id}/amend.

    Only the author can amend, and only while the original is still
    DRAFT or OUT_THERE. The amend creates a NEW proposal whose
    parent_proposal_id is the original; the original is moved to
    CANCELED so it stops collecting support and pulse processing.

    All edit fields are optional, but at least one must be provided
    or the amend is rejected (an amend that changes nothing is
    almost certainly a client bug).
    """
    user_id: uuid.UUID
    proposal_text: str | None = None
    pitch: str | None = None
    val_text: str | None = None
    val_uuid: uuid.UUID | None = None


class SupportCreate(BaseModel):
    user_id: uuid.UUID

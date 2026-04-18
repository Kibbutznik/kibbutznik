"""Replay harness — what-if analysis on a completed community.

True re-simulation with agents is expensive and non-deterministic (the LLM
makes different choices each time). Instead this script answers a simpler
but more useful question:

    Given the proposals + supports that actually landed in community X,
    if the governance variables had been Y instead, what WOULD have been
    accepted or rejected?

Use cases:
  - "Does raising PulseSupport from 50% to 60% reduce deadlock?"
  - "Would ThrowOut be easier to achieve at 40% rather than 60%?"
  - "How sensitive is proposal_throughput to ProposalSupport?"

Because it re-evaluates against fixed historical support counts, the
answer is a LOWER bound on what would have happened — agents would have
adjusted their behavior to the new thresholds. But it's deterministic,
fast (seconds, not minutes), and directly comparable across variable
sweeps.

Usage:
    python -m scripts.replay <community_id> \\
        --pulse-support 60 \\
        --proposal-support 30 \\
        [--output replays/<community_id>/summary.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Allow running as `python scripts/replay.py` without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kbz.database import async_session  # noqa: E402
from kbz.enums import MemberStatus, ProposalStatus  # noqa: E402
from kbz.models.member import Member  # noqa: E402
from kbz.models.proposal import Proposal  # noqa: E402
from kbz.models.support import Support  # noqa: E402
from kbz.services.metrics_service import MetricsService  # noqa: E402


@dataclass
class ReplayResult:
    community_id: str
    original: dict
    replayed: dict
    overrides: dict
    proposals_examined: int
    verdict_flips: int  # proposals that would have landed differently
    flip_detail: list[dict]


async def _active_member_count(db: AsyncSession, cid: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Member.user_id)).where(
                    Member.community_id == cid,
                    Member.status == MemberStatus.ACTIVE,
                )
            )
        ).scalar_one()
    )


def _required_support(member_count: int, pct: int) -> int:
    if member_count <= 0:
        return 1
    return max(1, math.ceil(member_count * pct / 100))


async def replay(
    community_id: uuid.UUID,
    *,
    pulse_support_pct: int | None = None,
    proposal_support_pct: int | None = None,
) -> ReplayResult:
    """Re-evaluate every historical proposal against new thresholds.

    `pulse_support_pct` is not directly applied per-proposal (it gates when
    a pulse fires) but is included in the summary for reference. The
    per-proposal replay uses `proposal_support_pct` (if supplied) against
    the historical support_count.
    """
    async with async_session() as db:
        mc = await _active_member_count(db, community_id)
        original_metrics = (await MetricsService(db).compute(community_id)).as_dict()

        threshold = (
            _required_support(mc, proposal_support_pct)
            if proposal_support_pct is not None
            else None
        )

        rows = (
            await db.execute(
                select(Proposal.id, Proposal.proposal_type, Proposal.proposal_status,
                       Proposal.support_count, Proposal.age)
                .where(Proposal.community_id == community_id)
            )
        ).all()

        # Rebuild a virtual "accepted" count under the override
        flipped: list[dict] = []
        virtual_accepted = 0
        virtual_rejected = 0
        virtual_canceled = 0
        for pid, ptype, status, support, age in rows:
            would_accept = (
                threshold is not None and (support or 0) >= threshold
            ) if threshold is not None else (status == ProposalStatus.ACCEPTED)

            if threshold is not None:
                if would_accept:
                    virtual_accepted += 1
                else:
                    # Pretend canceled/rejected stays as-is; only re-classify
                    # originally-accepted things that no longer clear quorum,
                    # and originally-rejected things that now would.
                    if status == ProposalStatus.ACCEPTED:
                        virtual_rejected += 1
                    else:
                        if status == ProposalStatus.REJECTED:
                            virtual_rejected += 1
                        else:
                            virtual_canceled += 1
            else:
                # No override: keep original classification
                if status == ProposalStatus.ACCEPTED:
                    virtual_accepted += 1
                elif status == ProposalStatus.REJECTED:
                    virtual_rejected += 1

            # Record flips
            originally_passed = status == ProposalStatus.ACCEPTED
            if threshold is not None and would_accept != originally_passed:
                flipped.append({
                    "proposal_id": str(pid),
                    "proposal_type": ptype,
                    "support_count": support,
                    "was": "ACCEPTED" if originally_passed else "NOT_ACCEPTED",
                    "now": "ACCEPTED" if would_accept else "NOT_ACCEPTED",
                })

        replayed = {
            "member_count": mc,
            "proposal_support_threshold": threshold,
            "virtual_accepted": virtual_accepted,
            "virtual_rejected": virtual_rejected,
            "virtual_canceled": virtual_canceled,
            "virtual_throughput": round(
                virtual_accepted / original_metrics["pulses_fired"], 3
            ) if original_metrics["pulses_fired"] else 0.0,
            "virtual_rejection_rate": round(
                virtual_rejected / (virtual_accepted + virtual_rejected), 3
            ) if (virtual_accepted + virtual_rejected) else 0.0,
        }

        return ReplayResult(
            community_id=str(community_id),
            original=original_metrics,
            replayed=replayed,
            overrides={
                "pulse_support_pct": pulse_support_pct,
                "proposal_support_pct": proposal_support_pct,
            },
            proposals_examined=len(rows),
            verdict_flips=len(flipped),
            flip_detail=flipped[:25],  # cap the summary file
        )


def _write_result(result: ReplayResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "community_id": result.community_id,
        "original": result.original,
        "replayed": result.replayed,
        "overrides": result.overrides,
        "proposals_examined": result.proposals_examined,
        "verdict_flips": result.verdict_flips,
        "flip_detail": result.flip_detail,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def _print_summary(result: ReplayResult) -> None:
    o, r = result.original, result.replayed
    print(f"\n=== Replay: {result.community_id} ===")
    print(f"Overrides: {result.overrides}")
    print(f"Members: {r['member_count']} | Pulses fired: {o['pulses_fired']}")
    print(f"Proposals examined: {result.proposals_examined}")
    print("\n                         Original → Replayed")
    print(f"  Accepted:              {o['proposals_accepted']:>4}     → {r['virtual_accepted']:>4}")
    print(f"  Rejected:              {o['proposals_rejected']:>4}     → {r['virtual_rejected']:>4}")
    print(f"  Throughput (acc/pulse): {o['proposal_throughput']:>5.2f}   → {r['virtual_throughput']:>5.2f}")
    print(f"  Rejection rate:        {o['rejection_rate']*100:>5.1f}%  → {r['virtual_rejection_rate']*100:>5.1f}%")
    print(f"\nVerdict flips: {result.verdict_flips}")
    if result.flip_detail:
        print("  (first 10 flips):")
        for f in result.flip_detail[:10]:
            print(f"    - {f['proposal_type']} [{f['proposal_id'][:8]}] "
                  f"support={f['support_count']}  {f['was']} → {f['now']}")


async def _main(args: argparse.Namespace) -> int:
    cid = uuid.UUID(args.community_id)
    result = await replay(
        cid,
        pulse_support_pct=args.pulse_support,
        proposal_support_pct=args.proposal_support,
    )
    _print_summary(result)
    output = args.output or f"replays/{cid}/summary.json"
    _write_result(result, Path(output))
    print(f"\nWrote {output}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Replay a community under different variables")
    p.add_argument("community_id", help="UUID of the community to replay")
    p.add_argument("--pulse-support", type=int, default=None,
                   help="Override PulseSupport percentage (e.g. 60)")
    p.add_argument("--proposal-support", type=int, default=None,
                   help="Override ProposalSupport percentage (e.g. 30)")
    p.add_argument("--output", default=None,
                   help="Path for the summary JSON (default: replays/<cid>/summary.json)")
    args = p.parse_args()
    # Ensure DB URL is set (mirrors alembic env.py behavior)
    if not os.environ.get("KBZ_DATABASE_URL"):
        os.environ["KBZ_DATABASE_URL"] = "postgresql+asyncpg://kbz:kbzpass@localhost:5432/kbz"
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()

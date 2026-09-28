from datetime import datetime, timezone

import pytest

from nifty500_source_news.design import NewsDesignControl, NewsDesignProposal
from nifty500_source_news.ledger import NewsLedger
from nifty500_source_news.policy import APPROVER_ID


NOW = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)


def _proposal():
    section = {"status": "test-only exact proposal"}
    return NewsDesignProposal(
        design_id="news-reference-v1",
        version="1",
        universe_policy=section,
        instrument_policy=section,
        source_categories=("primary_exchange_announcement",),
        candidate_policy=section,
        model_policy=section,
        timeframe_policy=section,
        entry_policy=section,
        exit_policy=section,
        reporting_policy=section,
        evidence="clean-room fixture",
        risks="fixture only",
        test_plan="unit test",
    )


def test_news_design_requires_exact_aditya_approval(tmp_path):
    control = NewsDesignControl(NewsLedger(tmp_path / "news.sqlite"))
    proposal = _proposal()
    value = control.propose(proposal, actor_id="engineer", at=NOW)
    with pytest.raises(ValueError, match="exact-hash approval"):
        control.activate(proposal.design_id, value, actor_id=APPROVER_ID, at=NOW)
    with pytest.raises(PermissionError, match="designated approver"):
        control.approve(
            proposal.design_id,
            value,
            actor_id="engineer",
            rationale="self approval",
            at=NOW,
        )
    control.approve(
        proposal.design_id,
        value,
        actor_id=APPROVER_ID,
        rationale="test-only exact approval",
        at=NOW,
    )
    control.activate(proposal.design_id, value, actor_id=APPROVER_ID, at=NOW)
    control.require_active(proposal.design_id, value)

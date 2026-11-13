from __future__ import annotations

import re
from decimal import Decimal
from html.parser import HTMLParser

import pytest

from apps.attendance_bot.app.jobs import monthly_stats


def _response(poll: int, user: int, seconds: object) -> dict:
    return {"poll_run_id": poll, "user_id": user, "response_seconds": seconds}


def _render(ranks: list[monthly_stats.FirstPlaceRank], **overrides) -> str:
    values = dict(
        year=2026,
        month=10,
        poll_count=20,
        expected_count=100,
        response_count=90,
        participation_pct=90,
        fastest=[],
        first_top=ranks,
        perfect_names=[],
    )
    values.update(overrides)
    return monthly_stats._build_report_html(**values)


class _BalancedHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        assert tag in {"b", "i", "blockquote"}
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.stack.pop() == tag


def _assert_valid_report(text: str) -> None:
    parser = _BalancedHTML()
    parser.feed(text)
    parser.close()
    assert parser.stack == []
    assert len(text) <= monthly_stats.MAX_REPORT_LENGTH
    assert len(text.encode("utf-16-le")) // 2 <= monthly_stats.MAX_REPORT_LENGTH
    # The admin preview adds this warning outside the report itself.
    warning = (
        "⚠️ <i>Исторический расчёт приблизительный: для части опросов "
        "нет зафиксированного снимка участников.</i>\n\n"
    )
    assert len((warning + text).encode("utf-16-le")) // 2 <= 4096


def test_first_place_credits_all_tied_winners_once_per_poll() -> None:
    rows = [
        _response(1, 1, Decimal("0.123456789123456789")),
        _response(1, 2, Decimal("0.123456789123456789")),
        _response(1, 2, Decimal("0.123456789123456789")),
        # A float conversion would erase this difference and invent a tie.
        _response(1, 3, Decimal("0.123456789123456790")),
    ]
    ranks = monthly_stats._rank_first_places(rows, {1: "Alpha", 2: "Beta", 3: "Gamma"})
    assert [(rank.user_id, rank.count, rank.rank) for rank in ranks] == [(1, 1, 1), (2, 1, 1)]
    assert all(rank.best_seconds == Decimal("0.123456789123456789") for rank in ranks)


def test_first_place_competition_rank_uses_only_winning_times() -> None:
    rows = [
        _response(1, 1, "1.0"),
        _response(1, 2, "1.0"),
        _response(2, 3, "1.5"),
        _response(3, 4, "2.0"),
        _response(4, 5, "0.01"),
        _response(4, 3, "0.02"),  # Fast losing answer must not improve user 3's winning time.
    ]
    ranks = monthly_stats._rank_first_places(rows, {1: "Alpha", 2: "Beta", 3: "Gamma", 4: "Delta", 5: "Epsilon"})
    assert [(rank.user_id, rank.rank, rank.best_seconds) for rank in ranks] == [
        (5, 1, Decimal("0.01")),
        (1, 2, Decimal("1.0")),
        (2, 2, Decimal("1.0")),
        (3, 4, Decimal("1.5")),
        (4, 5, Decimal("2.0")),
    ]
    rendered = _render(ranks)
    assert "🥇 Epsilon" in rendered
    assert "🥈 Alpha" in rendered and "🥈 Beta" in rendered
    assert "Gamma" not in rendered and "Delta" not in rendered
    assert "🥉" not in rendered


def test_number_of_wins_has_priority_over_winning_speed() -> None:
    ranks = monthly_stats._rank_first_places(
        [_response(1, 1, "20"), _response(2, 1, "10"), _response(3, 2, "0.001")],
        {1: "Alpha", 2: "Beta"},
    )
    assert [(rank.user_id, rank.count, rank.best_seconds, rank.rank) for rank in ranks] == [
        (1, 2, Decimal("10"), 1),
        (2, 1, Decimal("0.001"), 2),
    ]


@pytest.mark.parametrize("invalid", [None, 0, -1, "0", "-0.001", "NaN", "sNaN", "Infinity", "-Infinity", "invalid"])
def test_invalid_response_times_do_not_participate_in_ranking(invalid: object) -> None:
    ranks = monthly_stats._rank_first_places(
        [_response(1, 1, invalid), _response(1, 2, "0.000001")], {1: "Invalid", 2: "Valid"}
    )
    assert [(rank.user_id, rank.best_seconds) for rank in ranks] == [(2, Decimal("0.000001"))]


def test_first_place_text_preserves_precision_and_escapes_names() -> None:
    ranks = monthly_stats._rank_first_places(
        [_response(1, 1, "0.1000001"), _response(2, 2, "0.1000002")],
        {1: "<Alpha>&", 2: "Beta"},
    )
    rendered = _render(ranks)
    assert "&lt;Alpha&gt;&amp;" in rendered and "<Alpha>" not in rendered
    assert "0.1000001 сек." in rendered and "0.1000002 сек." in rendered
    assert monthly_stats._format_winning_seconds(Decimal("1E-7")) == "0.0000001 сек."
    assert monthly_stats._format_winning_seconds(Decimal("1.2000")) == "1.2 сек."
    _assert_valid_report(rendered)


def test_large_tied_leaderboard_discloses_omissions_without_changing_ranks() -> None:
    names = {user: f"Synthetic{user:04d}" for user in range(1, 301)}
    ranks = monthly_stats._rank_first_places(
        [_response(1, user, "0.100001") for user in names], names
    )
    assert len(ranks) == 300 and all(rank.rank == 1 for rank in ranks)
    rendered = _render(ranks, perfect_names=list(names.values()))
    leaderboard = rendered.split("🎯", 1)[1].split("✅", 1)[0]
    shown = leaderboard.count("🥇")
    omitted = re.search(r"не показано призёров: ([\d ]+)", leaderboard)
    assert omitted is not None
    assert shown + int(omitted[1].replace(" ", "")) == len(ranks)
    assert 0 < shown < len(ranks)
    assert "Список сокращён" in leaderboard
    _assert_valid_report(rendered)


def test_oversized_escaped_names_keep_sections_and_valid_html_with_explicit_omissions() -> None:
    name = "<Synthetic&>" * 1000
    ranks = [monthly_stats.FirstPlaceRank(1, name, 2, Decimal("0.1"), 1)]
    rendered = _render(ranks, fastest=[(name, 0.1)], perfect_names=[name])
    assert "не показано участников: 1" in rendered
    assert "не показано призёров: 1" in rendered
    assert "Ответили на все ожидаемые опросы" in rendered
    assert "<Synthetic" not in rendered
    _assert_valid_report(rendered)


@pytest.mark.parametrize("include_perfect", [False, True])
def test_astral_names_use_utf16_budget_in_tied_leaderboard(include_perfect: bool) -> None:
    name = "😀" * 200
    ranks = [monthly_stats.FirstPlaceRank(user, name, 1, Decimal("0.1"), 1) for user in range(30)]
    rendered = _render(ranks, perfect_names=[name] * 30 if include_perfect else [])
    leaderboard = rendered.split("🎯", 1)[1].split("✅", 1)[0]
    shown = leaderboard.count("🥇")
    omitted = re.search(r"не показано призёров: ([\d ]+)", leaderboard)
    assert omitted is not None
    assert 0 < shown < len(ranks)
    assert shown + int(omitted[1].replace(" ", "")) == len(ranks)
    _assert_valid_report(rendered)


def test_astral_perfect_list_uses_utf16_budget() -> None:
    rendered = _render([], perfect_names=[f"{user:02d}" + "😀" * 200 for user in range(30)])
    assert "<blockquote expandable>" in rendered
    assert "… и ещё" in rendered
    _assert_valid_report(rendered)


def test_impossible_astral_names_use_explicit_fallbacks_in_all_sections() -> None:
    name = "😀" * 4000
    ranks = [monthly_stats.FirstPlaceRank(1, name, 1, Decimal("0.1"), 1)]
    rendered = _render(ranks, fastest=[(name, 0.1)], perfect_names=[name])
    assert "не показано участников: 1" in rendered
    assert "не показано призёров: 1" in rendered
    assert "Список не помещается в сообщение: 1 человек" in rendered
    _assert_valid_report(rendered)


@pytest.mark.asyncio
async def test_full_report_uses_finite_times_but_preserves_response_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def source(*_args):
        return {
            "polls": [{"id": 1}],
            "expected": [{"poll_run_id": 1, "user_id": user, "full_name": f"Synthetic{user}"} for user in (1, 2, 3)],
            "responses": [_response(1, 1, "NaN"), _response(1, 2, "Infinity"), _response(1, 3, "0.25")],
        }

    monkeypatch.setattr(monthly_stats.MonthlyStatsRepo, "load_period", source)
    report = await monthly_stats.build_monthly_stats_report(2026, 10)
    assert report is not None
    assert report.response_count == report.expected_count == report.perfect_count == 3
    assert report.participation_pct == 100
    assert "🥇 Synthetic3" in report.html
    assert "🥇 Synthetic1" not in report.html and "🥈 Synthetic2" not in report.html
    _assert_valid_report(report.html)


def test_monthly_statistics_remain_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONTHLY_STATS_ENABLED", raising=False)
    assert monthly_stats.is_monthly_stats_enabled() is False
    monkeypatch.setenv("MONTHLY_STATS_ENABLED", "0")
    assert monthly_stats.is_monthly_stats_enabled() is False
    monkeypatch.setenv("MONTHLY_STATS_ENABLED", "1")
    assert monthly_stats.is_monthly_stats_enabled() is True

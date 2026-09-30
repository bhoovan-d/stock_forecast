import pytest
from fno_momentum.eligible_data import IneligibleRequest, split_segments, aligned_years, check_fresh


def _bar(day, t='00:00'):
    return [f'{day}T{t}:00+05:30', 1, 1, 1, 1, 1]


def test_a_segment_never_crosses_an_excluded_or_missing_session():
    units = ['2024-01-15', '2024-01-16', '2024-01-17', '2024-01-18', '2024-01-19']
    rows = [_bar('2024-01-15'), _bar('2024-01-16'), _bar('2024-01-18'), _bar('2024-01-19')]
    # 01-17 has no stored bar (missing session); 01-19 is excluded (e.g. quarantined).
    segs = split_segments(rows, units, excluded={'2024-01-19': 'quarantined'}, key=lambda r: r[0][:10])
    assert [[r[0][:10] for r in s] for s in segs] == [['2024-01-15', '2024-01-16'], ['2024-01-18']]


def test_intraday_session_with_an_absent_start_is_dropped_whole():
    units = ['2022-03-04', '2022-03-07', '2022-03-08']
    rows = [_bar('2022-03-04', '09:15'), _bar('2022-03-07', '09:15'), _bar('2022-03-07', '09:20'), _bar('2022-03-08', '09:15')]
    segs = split_segments(rows, units, excluded={'2022-03-07': 'absent bar start(s)'}, key=lambda r: r[0][:10])
    assert [[r[0] for r in s] for s in segs] == [['2022-03-04T09:15:00+05:30'], ['2022-03-08T09:15:00+05:30']]


def test_a_split_session_becomes_two_segments():
    from fno_momentum.eligible_data import split_within_sessions
    # 2 Mar 2024: NSE traded 09:15-10:00 and 11:30-12:30 with the market shut between.
    seg = [_bar('2024-03-01', '15:15'), _bar('2024-03-02', '09:15'), _bar('2024-03-02', '09:30'),
           _bar('2024-03-02', '11:30'), _bar('2024-03-02', '11:45')]
    out = split_within_sessions([seg], minutes=15)
    assert [[r[0][11:16] for r in s] for s in out] == [['15:15', '09:15', '09:30'], ['11:30', '11:45']]


def test_breaks_split_even_when_no_unit_is_missing():
    # An unresolved calendar day is not a session we know of, but a session may hide there.
    units = ['2010-03-05', '2010-03-09']
    rows = [_bar('2010-03-05'), _bar('2010-03-09')]
    segs = split_segments(rows, units, excluded={}, key=lambda r: r[0][:10], breaks=['2010-03-08'])
    assert len(segs) == 2


def test_daily_and_intraday_are_only_combined_in_years_on_one_price_basis():
    basis = {'2023': {'differs': True}, '2024': {'differs': False}}
    assert aligned_years(basis, ['2023', '2024', '2025']) == ['2024']


def test_a_stale_eligibility_manifest_is_refused():
    with pytest.raises(IneligibleRequest, match='catalog changed'):
        check_fresh({'catalog_sha256': 'old'}, 'new', None, None)
    with pytest.raises(IneligibleRequest, match='NSE'):
        check_fresh({'catalog_sha256': 'a', 'nse_catalog_sha256': 'x'}, 'a', 'y', None)

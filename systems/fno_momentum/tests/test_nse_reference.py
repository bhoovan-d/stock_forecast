from fno_momentum.nse_reference import Reference, calendar_checks, compare_daily, derive_weekly, pick_row


def _ref(days, rows=None, listing=None):
    return Reference(days=days, stock_rows=rows or {}, listing=listing or {}, errors=[])


def test_calendar_separates_holidays_missing_sessions_and_conflicts():
    days = {'2024-01-19': 'PUBLISHED', '2024-01-20': 'PUBLISHED', '2024-01-21': 'NOT_PUBLISHED',
            '2024-01-22': 'NOT_PUBLISHED', '2024-01-23': 'PUBLISHED', '2024-01-24': 'PUBLISHED', '2024-01-25': 'SOURCE_FAILURE',
            '2024-01-26': 'NOT_PUBLISHED'}
    rows = {'ABC': {'2024-01-19': {}, '2024-01-20': {}, '2024-01-23': {}}}
    stored = {'2024-01-19', '2024-01-22', '2024-01-26'}
    c = calendar_checks(stored, '2024-01-19', '2024-01-26', _ref(days, rows), 'ABC')
    assert c['missing_sessions'] == ['2024-01-20', '2024-01-23']      # NSE traded the stock; nothing stored
    assert c['untraded_sessions'] == ['2024-01-24']                     # session, but no NSE row for the stock
    assert c['conflicts'] == ['2024-01-22', '2024-01-26']               # stored bars on a day NSE published nothing
    assert c['unresolved_days'] == ['2024-01-25']                       # a failed fetch proves nothing
    assert c['verified'] is False


def test_quarantined_days_are_not_double_counted_as_missing_sessions():
    days = {'2024-08-30': 'PUBLISHED'}
    c = calendar_checks(set(), '2024-08-30', '2024-08-30', _ref(days, {'IDEA': {'2024-08-30': {}}}), 'IDEA', quarantined={'2024-08-30'})
    assert c['missing_sessions'] == [] and c['quarantined_sessions'] == ['2024-08-30']
    # Also when NSE has no matched row for the stock that day (older symbol/ISIN).
    c = calendar_checks(set(), '2005-01-12', '2005-01-12', _ref({'2005-01-12': 'PUBLISHED'}), 'VEDL', quarantined={'2005-01-12'})
    assert c['quarantined_sessions'] == ['2005-01-12'] and c['untraded_sessions'] == []


def test_series_preference_and_daily_comparison_keep_both_values():
    assert pick_row([{'series': 'N1'}, {'series': 'BE'}, {'series': 'EQ'}])['series'] == 'EQ'
    assert pick_row([{'series': 'N1'}]) is None
    stored = [['2024-01-19T00:00:00+05:30', 10, 11, 9, 10.5, 100], ['2024-01-22T00:00:00+05:30', 10, 11, 9, 10.5, 100]]
    nse = {'2024-01-19': {'open': 10, 'high': 11, 'low': 9, 'close': 10.5, 'volume': 100, 'series': 'EQ'},
           '2024-01-22': {'open': 10, 'high': 11, 'low': 9, 'close': 10.6, 'volume': 90, 'series': 'EQ'}}
    r = compare_daily(stored, nse)
    assert r['compared'] == 2 and r['identical'] == 1 and r['field_diffs'] == {'close': 1, 'volume': 1}
    assert r['mismatches'][0]['stored'][3] == 10.5 and r['mismatches'][0]['nse']['close'] == 10.6


def test_weekly_needs_every_session_of_the_week():
    rows = [['2024-01-15T00:00:00+05:30', 1, 3, 1, 2, 10], ['2024-01-16T00:00:00+05:30', 2, 4, 2, 3, 20],
            ['2024-01-22T00:00:00+05:30', 3, 5, 3, 4, 30]]
    sessions = ['2024-01-15', '2024-01-16', '2024-01-22', '2024-01-23']
    w = derive_weekly(rows, sessions)
    assert [r[0] for r in w['rows']] == ['2024-01-15'] and w['incomplete_weeks'] == ['2024-01-22']


def test_a_week_with_an_unresolved_calendar_day_is_left_empty():
    rows = [['2024-01-15T00:00:00+05:30', 1, 3, 1, 2, 10], ['2024-01-16T00:00:00+05:30', 2, 4, 2, 3, 20]]
    # 2024-01-20 (Saturday) could not be resolved: that week may hide a session.
    w = derive_weekly(rows, ['2024-01-15', '2024-01-16'], unresolved=['2024-01-20'])
    assert w['rows'] == [] and w['unresolved_weeks'] == ['2024-01-15']

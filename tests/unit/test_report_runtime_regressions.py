from eoa.report.indicators import render_watchlist_table


def test_watchlist_collapses_exact_repeats_without_merging_other_claims():
    rows = [
        {"text_he": "Delivery of 280 sensors in 2029", "_row_status": "open"},
        {"text_he": "Delivery of 280 sensors in 2029", "_row_status": "open"},
        {"text_he": "Delivery of 300 sensors in 2030", "_row_status": "open"},
        {"text_he": "Delivery of 280 sensors in 2029", "_row_status": "matured"},
    ]
    section = render_watchlist_table(rows, [], [])
    assert len(section["body_he"].splitlines()) == 5
    assert "300 sensors in 2030" in section["body_he"]
    assert len(rows) == 4

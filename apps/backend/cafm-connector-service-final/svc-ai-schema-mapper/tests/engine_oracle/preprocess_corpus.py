"""Tables that exercise every rule of Node 5's cleaning (preprocess_node.preprocess_tables).

Each case is (name, tables, rename map by table, skip fields by table). Values are what the parse
hands preprocess: strings or None.
"""
from __future__ import annotations

N = None


def _cols(**cols) -> list[dict]:
    """Records from columns of equal length."""
    names = list(cols)
    return [dict(zip(names, vals)) for vals in zip(*cols.values())]


def cases() -> list[tuple[str, dict, dict, dict]]:
    out: list[tuple[str, dict, dict, dict]] = []

    out.append(("duplicates", {
        "T": [{"a": "1", "b": None}, {"a": "1", "b": None}, {"a": "1", "b": ""}, {"a": "2", "b": "x"},
              {"a": "2", "b": "x"}, {"a": " 2", "b": "x"}],
        "Empty": [],
    }, {}, {}))

    out.append(("all_null_columns", {
        "T": _cols(keep=["1", "2", "3"], gone=[N, N, N], also_gone=[N, N, N], part=[N, "x", N]),
    }, {}, {}))

    out.append(("numeric_inference", {
        "Nums": _cols(
            plain=["5", N, "7"], spaced=[" 5 ", N, "6"], exp=["1e3", N, "2"], comma=["1,000", N, "2"],
            inf=["inf", N, "1"], minus_inf=["-inf", N, "1"], infinity=["Infinity", N, "1"], boolean=["True", N, "False"],
            arabic=["٣", N, "4"], fullwidth=["５", N, "1"], signed=["-5", N, "+5"], dotted=[".5", N, "5."],
            underscore=["1_000", N, "2"], hexa=["0x10", N, "2"], huge=["1e400", N, "1"], tiny=["1.5e-3", N, "0"],
            neg_zero=["-0", N, "0"], blank=["  ", N, "1"], empty=["", N, "1"], decimal_comma=["1,5", N, "2"],
            mixed_text=["5", N, "five"], leading_zero=["007", N, "08"], tabbed=["\t5", N, "6\n"],
        ),
    }, {}, {}))

    out.append(("text_and_date_inference", {
        "Infer": _cols(
            ten_dates=["2025-01-%02d" % d for d in range(1, 11)] + ["later text"],
            half_dates=["2025-01-01", "x", "2025-01-03", "y", "2025-01-05", "z", "2025-01-07", "w", "2025-01-09", "v", "u"],
            under_half=["2025-01-01", "x", "y", "z", "2025-01-05", "w", "v", "t", "s", "r", "q"],
            dash_short=["1-2-3", "4-5-6", N, "7-8-9", "10-11-12", "1/2/3", "2024/1/2", "x", "y", "z", "w"],
            arabic_digits=["٢٠٢٥-٠١-٠١", "٢٠٢٥-٠١-٠٢", N, "x", "y", "z", "w", "v", "u", "t", "s"],
            spaced_dates=[" 2025-01-01 ", "2025-01-02", N, "x", "y", "z", "w", "v", "u", "t", "s"],
            arabic_date=["٢٠٢٥-٠١-٠%s" % d for d in "١٢٣٤٥٦"] + [N, "x", "y", "z", "w"],
            spaced_date=[" 2025-01-0%d " % d for d in range(1, 7)] + [N, "x", "y", "z", "w"],
            newline_date=["2025-01-0%d\n" % d for d in range(1, 7)] + [N, "x", "y", "z", "w"],
        ),
    }, {}, {}))

    out.append(("date_formats", {
        "Dates": _cols(
            iso_date=["2025-12-31", "2026-01-05", N, "2026-1-5"],
            dmy_slash_date=["31/12/2025", "05/01/2026", N, "5/1/2026"],
            mdy_slash_date=["12/31/2025", "01/13/2026", N, "1/13/2026"],
            ymd_slash_date=["2025/12/31", "2026/01/05", N, "2026/1/5"],
            dmy_dash_date=["31-12-2025", "05-01-2026", N, "5-1-2026"],
            iso_time_created=["2026-07-10T14:30:00", "2026-07-10T09:05:07", N, "2026-07-11T00:00:00"],
            iso_space_created=["2026-07-10 14:30:00", "2026-07-10 00:00:00", N, "2026-07-11 23:59:59"],
            excel_midnight_date=["2026-07-10 00:00:00", "2026-07-13 00:00:00", N, "2026-12-01 00:00:00"],
            iso_fraction_time=["2026-07-10 14:30:00.5", "2026-07-10 14:30:00.123456", N, "2026-07-10 14:30:01"],
            dayfirst_time_date=["10/07/2026 14:30", "13/07/2026 09:05", N, "01/08/2026 00:00"],
            monthfirst_date=["07/13/2026", "07/14/2026", N, "12/25/2026"],
            year_slash_time_date=["2026/07/10 14:30:00", "2026/07/11 09:00:00", N, "2026/12/01 00:00:00"],
            month_name_date=["31 Dec 2025", "1 Jan 2026", N, "15 Feb 2026"],
            month_first_name_date=["Dec 31, 2025", "Jan 1, 2026", N, "Feb 15, 2026"],
            full_month_date=["31 December 2025", "1 January 2026", N, "15 February 2026"],
            dash_month_date=["31-Dec-2025", "01-Jan-2026", N, "15-Feb-2026"],
            two_digit_year_date=["10/07/26", "13/07/26", N, "01/08/26"],
            dotted_date=["10.07.2026", "13.07.2026", N, "01.08.2026"],
            ampm_time=["10/07/2026 2:30 PM", "13/07/2026 9:05 AM", N, "01/08/2026 12:00 AM"],
            space_day_date=["2025-12- 1", "2025-12-02", N, "2025-12-03"],
        ),
    }, {}, {}))

    six = {
        "iso_date": ["2025-12-31", "2026-01-05", "2026-1-5", "2026-02-28", "2024-02-29", "2026-12-01"],
        "dmy_slash_date": ["31/12/2025", "05/01/2026", "5/1/2026", "28/02/2026", "29/02/2024", "01/12/2026"],
        "mdy_slash_date": ["12/31/2025", "01/13/2026", "1/13/2026", "02/28/2026", "02/29/2024", "12/01/2026"],
        "ymd_slash_date": ["2025/12/31", "2026/01/05", "2026/1/5", "2026/02/28", "2024/02/29", "2026/12/01"],
        "dmy_dash_date": ["31-12-2025", "05-01-2026", "5-1-2026", "28-02-2026", "29-02-2024", "01-12-2026"],
        "iso_time_created": ["2026-07-10T14:30:00", "2026-07-10T09:05:07", "2026-07-11T00:00:00",
                             "2026-07-12T23:59:59", "2026-07-13T12:00:00", "2026-07-14T01:02:03"],
        "iso_space_created": ["2026-07-10 14:30:00", "2026-07-10 00:00:00", "2026-07-11 23:59:59",
                              "2026-07-12 01:00:00", "2026-07-13 12:00:00", "2026-07-14 06:07:08"],
        "excel_midnight_date": ["2026-07-10 00:00:00", "2026-07-13 00:00:00", "2026-12-01 00:00:00",
                                "2026-01-31 00:00:00", "2024-02-29 00:00:00", "2026-03-15 00:00:00"],
        "iso_fraction_time": ["2026-07-10 14:30:00.5", "2026-07-10 14:30:00.123456", "2026-07-10 14:30:01",
                              "2026-07-10 14:30:01.000001", "2026-07-10T14:30:02.25", "2026-07-10 14:30:03"],
        "dayfirst_time_date": ["10/07/2026 14:30", "13/07/2026 09:05", "01/08/2026 00:00", "31/12/2026 23:59",
                               "02/01/2026 12:00", "15/06/2026 06:30"],
        "monthfirst_date": ["07/13/2026", "07/14/2026", "12/25/2026", "01/31/2026", "02/28/2026", "06/15/2026"],
        "year_slash_time_date": ["2026/07/10 14:30:00", "2026/07/11 09:00:00", "2026/12/01 00:00:00",
                                 "2026/01/31 23:59:59", "2024/02/29 12:00:00", "2026/06/15 06:30:00"],
        "month_name_date": ["31 Dec 2025", "1 Jan 2026", "15 Feb 2026", "28 Feb 2026", "29 Feb 2024", "1 Dec 2026"],
        "month_first_name_date": ["Dec 31, 2025", "Jan 1, 2026", "Feb 15, 2026", "Feb 28, 2026", "Feb 29, 2024",
                                  "Dec 1, 2026"],
        "full_month_date": ["31 December 2025", "1 January 2026", "15 February 2026", "28 February 2026",
                            "29 February 2024", "1 December 2026"],
        "dash_month_date": ["31-Dec-2025", "01-Jan-2026", "15-Feb-2026", "28-Feb-2026", "29-Feb-2024", "01-Dec-2026"],
        "two_digit_year_date": ["10/07/26", "13/07/26", "01/08/26", "31/12/26", "29/02/24", "15/06/26"],
        "dotted_date": ["10.07.2026", "13.07.2026", "01.08.2026", "31.12.2026", "29.02.2024", "15.06.2026"],
        "ampm_time": ["10/07/2026 2:30 PM", "13/07/2026 9:05 AM", "01/08/2026 12:00 AM", "31/12/2026 11:59 PM",
                      "02/01/2026 12:00 PM", "15/06/2026 6:30 AM"],
        "space_day_date": ["2025-12- 1", "2025-12-02", "2025-12-03", "2025-12- 4", "2025-12-05", "2025-12-06"],
        "ambiguous_dayfirst_date": ["01/02/2026", "03/04/2026", "05/06/2026", "07/08/2026", "09/10/2026", "11/12/2026"],
        "year_first_dayfirst_due": ["2026-13-01", "2026-14-02", "2026-15-03", "2026-16-04", "2026-17-05", "2026-18-06"],
    }
    out.append(("date_formats_no_nulls", {"Dates6": _cols(**six)}, {}, {}))

    out.append(("the_80_percent_edge", {
        "Edge": _cols(
            four_of_five_date=["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "not a date"],
            date_code=["DC-001", "DC-002", "DC-003", "DC-004", "DC-005"],
            due_days=["5", "7", N, "10", "12"],
            time_spent=["1.5", "2", N, "0.25", "3"],
            completed_flag=["yes", "no", N, "yes", "no"],
            created_by=["ann", "bob", N, "cy", "di"],
        ),
        "Edge10": _cols(
            seven_of_ten_date=["2025-01-%02d" % d for d in range(1, 8)] + ["x", "y", "z"],
            eight_of_ten_date=["2025-01-%02d" % d for d in range(1, 9)] + ["x", "y"],
            nine_dayfirst_date=["%02d/01/2025" % d for d in range(13, 22)] + ["junk"],
        ),
    }, {}, {}))

    out.append(("rename_collisions_and_skips", {
        "Assets": _cols(tag=["A-1", "A-2", "A-3"], code=["C-1", "C-2", "C-3"], name=["n1", "n2", "n3"],
                        junk=["j", "j", "j"], installed=["2024-01-15", "2024-02-15", "2024-03-15"]),
        "Sites": _cols(site=["S1", "S2"], label=["x", None]),
    }, {"Assets": {"tag": "asset_code", "code": "asset_code", "installed": "install_date", "absent": "nowhere"},
        "Sites": {"site": "site_code"}},
        {"Assets": {"junk", "not_there"}, "Sites": {"site"}}))

    # A table that loses every column: to_dict(orient="records") on rows with no columns is [],
    # so Python hands the writer nothing for it (and the writer writes nothing).
    out.append(("every_column_removed", {
        "Blank": _cols(a=[N, N, N], b=[N, "", N]),
        "Skipped": _cols(a=["1", "2", "2"], b=["x", "y", "y"]),
        "Kept": _cols(a=["1", "2", "3"]),
    }, {}, {"Skipped": ["a", "b"]}))

    # Two-digit years (no format pandas can guess, so dateutil reads each value) with a zone on some
    # values: pandas leaves datetime objects, and a blank becomes the text "NaT", which counts
    # as a value — on a column a third empty too.
    zoned = ["28/03/26 10:00Z", "29/03/26 11:00", "30/03/26 12:00", "31/03/26 13:00Z", "01/04/26 09:00", "02/04/26 10:00"]
    out.append(("mixed_zone_dates", {
        "Zoned": _cols(wo=[str(i) for i in range(6)], completed_date=zoned),
        "ZonedGaps": _cols(wo=[str(i) for i in range(10)],
                           completed_date=[zoned[0], "", zoned[1], "", zoned[2], "", zoned[3], zoned[4], zoned[5], ""]),
    }, {}, {}))

    out.append(("el_m5_below_80_percent", {
        "Dupes": [{"a": "1"}] * 8 + [{"a": "2"}, {"a": "3"}],
        "Fine": [{"a": "1"}, {"a": "2"}],
    }, {}, {}))

    return out

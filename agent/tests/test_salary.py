"""The scanner's salary parser suite (job-scanner tests/test_salary.py, edd1528), verbatim cases.

Moved out of test_filter_rules.py (2026-10-24) because it needs no search profile: that module needs the
private scanner config and is left out of public CI, and these cases should run everywhere.
"""
import pytest

from jobforge_agent.filtering.salary import parse_salary


@pytest.mark.parametrize("text, lo, hi, period, open_ended", [
    ("The pay range is $85,000 - $100,000 per year.", 85000, 100000, "YEAR", False),
    ("$25 - $29 an hour", 25, 29, "HOUR", False),
    ("Pay: $25.50 to $29.75 per hour", 25.5, 29.75, "HOUR", False),
    ("From $130,000 a year", 130000, None, "YEAR", True),
    ("Up to $55,000 annually", None, 55000, "YEAR", False),
    ("Salary: $90K-$110K", 90000, 110000, "YEAR", False),
    ("Salary: $90-110K", 90000, 110000, "YEAR", False),
    ("Compensation – $72,000 – $88,000 USD", 72000, 88000, "YEAR", False),
    ("Range: $72,000—$88,000", 72000, 88000, "YEAR", False),
    ("$5,000 - $6,500 per month", 5000, 6500, "MONTH", False),
    ("Starting at $22/hr", 22, None, "HOUR", True),
    ("We pay $31 per hour.", 31, 31, "HOUR", False),
])
def test_salary_formats(text, lo, hi, period, open_ended):
    s = parse_salary(text)
    assert s is not None, text
    assert (s.min, s.max, s.period, s.open_ended) == (lo, hi, period, open_ended)


def test_salary_geo_tiers_merge_to_widest_span():
    s = parse_salary("Zone A: $95,000 - $120,000. Zone B: $80,000 - $100,000.")
    assert (s.min, s.max, s.period) == (80000, 120000, "YEAR")


@pytest.mark.parametrize("text", ["We raised $50 million in our Series B.", "Revenue of $5 billion",
                                  "$500 home office stipend", "Pay is CA$80,000 - CA$95,000",
                                  "No salary listed here.", "", None])
def test_salary_ignores_non_salary_dollars(text):
    assert parse_salary(text) is None


def test_salary_annual_max():
    assert parse_salary("$28 - $30 an hour").annual_max() == 30 * 2080
    assert parse_salary("From $50,000 per year").annual_max() is None

from backend.app.models import (
    AnnualSnapshot,
    CompanyFinancials,
    FactValue,
    QuarterlySnapshot,
)
from backend.app.scoring import score_company, PASS, FAIL, UNAVAILABLE


def _fv(value):
    return FactValue(value=value, available=True, concept="TestConcept", form="10-Q", period_end="2026-06-30")


def make_healthy_company() -> CompanyFinancials:
    q = QuarterlySnapshot(
        cash_and_equivalents=_fv(50_000_000_000),
        short_term_investments=_fv(20_000_000_000),
        long_term_investments=_fv(10_000_000_000),
        short_term_debt=_fv(5_000_000_000),
        long_term_debt=_fv(20_000_000_000),
        total_liabilities=_fv(60_000_000_000),
        total_equity=_fv(100_000_000_000),
        treasury_stock=FactValue.missing(),
        preferred_stock=_fv(0),
        retained_earnings=_fv(40_000_000_000),
        operating_lease_current=_fv(1_000_000_000),
        operating_lease_noncurrent=_fv(3_000_000_000),
        finance_lease_current=FactValue.missing(),
        finance_lease_noncurrent=FactValue.missing(),
    )
    a = AnnualSnapshot(
        fiscal_year=2025,
        retained_earnings=_fv(40_000_000_000),
        prior_retained_earnings=_fv(35_000_000_000),
        operating_cash_flow=_fv(30_000_000_000),
        capital_expenditures=_fv(-8_000_000_000),
        operating_income=_fv(25_000_000_000),
        interest_expense=_fv(1_000_000_000),
        repurchases_of_stock=FactValue.missing(),
        treasury_stock=FactValue.missing(),
    )
    return CompanyFinancials(ticker="TEST", cik=1, name="Test Co", quarterly=q, annual=a)


def make_sparse_company() -> CompanyFinancials:
    q = QuarterlySnapshot()
    a = AnnualSnapshot()
    return CompanyFinancials(ticker="SPARSE", cik=2, name="Sparse Co", quarterly=q, annual=a)


def test_healthy_company_scores_high_and_passes_core_rules():
    cf = make_healthy_company()
    result = score_company(cf)
    assert result["overall_score"] is not None
    assert result["overall_score"] >= 80
    assert result["label"] in ("Strong", "Healthy")
    assert "liquidity" in result["passed_rules"]
    assert "retained_earnings" in result["passed_rules"]
    assert "retained_earnings_growth" in result["passed_rules"]
    assert "free_cash_flow" in result["passed_rules"]


def test_sparse_company_marks_rules_unavailable_not_passed():
    cf = make_sparse_company()
    result = score_company(cf)
    assert result["label"] == "Insufficient Data"
    assert len(result["unavailable_rules"]) == 9
    assert result["passed_rules"] == []
    assert result["points_available_scored"] == 0


def test_negative_liquidity_fails():
    cf = make_healthy_company()
    cf.quarterly.long_term_debt = _fv(200_000_000_000)
    result = score_company(cf)
    by_id = {r["id"]: r for r in result["rules"]}
    assert by_id["liquidity"]["status"] == FAIL


def test_unavailable_rule_earns_zero_and_is_excluded_from_denominator():
    cf = make_healthy_company()
    cf.quarterly.total_liabilities = FactValue.missing()
    result = score_company(cf)
    by_id = {r["id"]: r for r in result["rules"]}
    assert by_id["debt_to_equity"]["status"] == UNAVAILABLE
    assert by_id["debt_to_equity"]["points_earned"] == 0
    assert by_id["debt_to_equity"]["points_available"] == 0

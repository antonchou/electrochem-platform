import math
import random

import pytest

from ec.chemistry import kcl_kappa25_us_cm
from ec.fitting import FitError, cell_constant_fit, concentration_fit, fit_line, t95, temperature_fit


def test_line_fit_golden_values():
    # y = 2x + 1 加一组与 x 正交、和为 0 的残差：斜率/截距精确，SSres = 0.04
    xs = [1, 2, 3, 4, 5]
    residuals = [0.1, -0.1, -0.1, 0.1, 0.0]
    fit = fit_line(xs, [2 * x + 1 + r for x, r in zip(xs, residuals)])
    assert fit.slope == pytest.approx(2.0)
    assert fit.intercept == pytest.approx(1.0)
    assert fit.residuals == pytest.approx(residuals)
    s2 = 0.04 / 3
    assert fit.slope_se == pytest.approx(math.sqrt(s2 / 10))  # Sxx = 10
    assert fit.intercept_se == pytest.approx(math.sqrt(s2 * (1 / 5 + 9 / 10)))
    assert fit.r2 == pytest.approx(1 - 0.04 / 40.04)
    ci = fit.param(fit.slope, fit.slope_se)["ci95"]
    assert ci == pytest.approx([2 - 3.182 * fit.slope_se, 2 + 3.182 * fit.slope_se])


def test_through_origin():
    fit = fit_line([1.0, 2.0, 4.0], [2.0, 4.0, 8.0], through_origin=True)
    assert fit.slope == pytest.approx(2.0)
    assert fit.intercept == 0.0
    assert fit.slope_se == pytest.approx(0.0)
    assert fit_line([3.0], [6.0], through_origin=True).slope == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("xs", "ys"),
    [([1.0], [1.0]), ([2.0, 2.0], [1.0, 3.0]), ([1.0, math.nan], [1.0, 2.0])],
)
def test_degenerate_line_fits_raise(xs, ys):
    with pytest.raises(FitError):
        fit_line(xs, ys)


def test_t95_table_and_expansion():
    assert t95(1) == 12.706
    assert t95(30) == 2.042
    assert t95(40) == pytest.approx(2.021, abs=1e-3)
    assert t95(120) == pytest.approx(1.980, abs=1e-3)


def test_concentration_fit_recovers_kohlrausch_parameters_for_kcl():
    concentrations = [0.5, 1.0, 2.0, 5.0, 10.0]
    fit = concentration_fit([(c, kcl_kappa25_us_cm(c)) for c in concentrations])
    k = fit["kohlrausch"]
    assert k["ok"]
    assert k["lambda0_s_cm2_per_mol"]["value"] == pytest.approx(149.8, abs=1.0)  # KCl 无限稀释摩尔电导率
    assert 80 < k["k_s_cm2_per_mol_sqrt_l_per_mol"]["value"] < 100  # Kohlrausch 常数量级
    assert fit["linear"]["ok"] and fit["linear"]["r2"] > 0.999
    assert fit["range_mmol_l"] == [0.5, 10.0]


def test_kohlrausch_needs_three_positive_concentrations():
    fit = concentration_fit([(0.0, 1.5), (1.0, 147.0), (10.0, 1413.0)])
    assert fit["linear"]["ok"]
    assert not fit["kohlrausch"]["ok"]


def test_temperature_fit_recovers_alpha():
    rnd = random.Random(3)
    points = [(t, 1413.0 * (1 + 0.0195 * (t - 25.0)) * (1 + rnd.gauss(0, 1e-4))) for t in [20 + 0.25 * i for i in range(41)]]
    fit = temperature_fit(points)
    assert fit["ok"]
    assert fit["alpha_per_c"]["value"] == pytest.approx(0.0195, rel=0.01)
    assert fit["kappa25_us_cm"]["value"] == pytest.approx(1413.0, rel=1e-3)
    lo, hi = fit["alpha_per_c"]["ci95"]
    assert lo < 0.0195 < hi


def test_temperature_fit_refuses_narrow_range():
    fit = temperature_fit([(25.0 + 0.01 * i, 1413.0) for i in range(20)])
    assert not fit["ok"] and "温度跨度" in fit["error"]


def test_cell_constant_from_standards():
    # 真 Kcell = 1.02：测得 G25 = κ标准 / Kcell
    standards = [147.0, 1413.0, 12880.0]
    fit = cell_constant_fit([(k * 1e-6 / 1.02, k) for k in standards])
    assert fit["cell_constant_per_cm"] == pytest.approx(1.02)
    assert fit["deviations_pct"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
    single = cell_constant_fit([(1413e-6 / 0.98, 1413.0)])
    assert single["cell_constant_per_cm"] == pytest.approx(0.98)
    assert single["r2"] is None


def test_cell_constant_rejects_zero_conductance():
    with pytest.raises(FitError):
        cell_constant_fit([(0.0, 1413.0)])

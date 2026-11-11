"""Pre-specified analysis. Written and tested BEFORE any study data exists.

Specification decisions, and why each one is not the obvious default:

- **Item fixed effects, not cluster-robust SEs.** The design has ~60 items, and
  cluster-robust variance is badly downward-biased below roughly 40-50 clusters:
  it would over-reject and manufacture significance. Items are crossed into
  every cell, so item dummies absorb difficulty entirely with no distributional
  assumption and no incidental-parameters problem for the within-item contrasts.

- **The primary outcome is continuous.** `leak_hat` in [-1, 1] is analysed
  directly. Thresholding it into a binary first discards power and invites the
  "where did the cutoff come from" objection; the binary is a secondary.

- **Wilson, never Wald.** Wald intervals on proportions near 0 or 1 -- exactly
  where leakage rates sit -- produce bounds outside [0, 1] and undercover badly.

- **Holm within pre-enumerated families, never pooled.** The families are fixed
  in config-adjacent documentation before data, so the multiplicity correction
  cannot be chosen after seeing which contrasts came out.

- **Injected-context drift is a covariate, not an assumption.** The hosted
  path's hidden prompt grew ~2,000 tokens within one day, so prompt_tokens
  enters the model rather than being waved away as constant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ---------------------------------------------------------------- intervals

def wilson_ci(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score interval. Correct near 0 and 1, where leakage rates live."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def mean_ci(values: list[float], z: float = 1.959963984540054) -> tuple[float, float, float]:
    """Mean with a normal-approximation CI, for the continuous outcome."""
    n = len(values)
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    m = sum(values) / n
    if n == 1:
        return (m, float("nan"), float("nan"))
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    se = math.sqrt(var / n)
    return (m, m - z * se, m + z * se)


# ---------------------------------------------------------------- multiplicity

def holm_bonferroni(pvalues: dict[str, float], alpha: float = 0.05) -> dict[str, dict]:
    """Holm step-down within ONE family.

    Never call this on contrasts pooled across families: the families are
    pre-enumerated precisely so the correction cannot be tuned after the fact.
    """
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, dict] = {}
    rejected_so_far = True
    for i, (name, p) in enumerate(items):
        threshold = alpha / (m - i)
        if rejected_so_far and p <= threshold:
            reject = True
        else:
            reject = False
            rejected_so_far = False  # step-down: stop at the first failure
        out[name] = {
            "p": p, "threshold": threshold, "reject": reject,
            "p_adjusted": min(1.0, p * (m - i)),
        }
    return out


# ---------------------------------------------------------------- power

def two_proportion_mde(
    n_per_cell: int, p1: float = 0.5, alpha: float = 0.05, power: float = 0.80,
) -> float:
    """Minimum detectable difference for a two-proportion contrast.

    Reported in the manuscript rather than hidden: at 20 items per cell the MDE
    after Holm over 15 model pairs is ~0.50, which is why the model factor is
    reported descriptively instead of tested pairwise.
    """
    z_a = _z(1 - alpha / 2)
    z_b = _z(power)
    lo, hi = 0.0, 1.0 - p1
    for _ in range(200):
        d = (lo + hi) / 2
        p2 = min(1.0, p1 + d)
        pbar = (p1 + p2) / 2
        num = z_a * math.sqrt(2 * pbar * (1 - pbar)) + z_b * math.sqrt(
            p1 * (1 - p1) + p2 * (1 - p2))
        needed = (num / d) ** 2 if d > 0 else float("inf")
        if needed > n_per_cell:
            lo = d
        else:
            hi = d
    return hi


def _z(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation, ~1e-9 accurate)."""
    if not 0 < p < 1:
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ---------------------------------------------------------------- regression

@dataclass
class FitResult:
    params: dict[str, float]
    pvalues: dict[str, float]
    n_obs: int
    n_items: int
    converged: bool
    note: str = ""


def _nested_in_units(df, factor: str, unit: str = "item_id") -> bool:
    """True when `factor` never varies inside a `unit` -- so the unit dummies
    already span it. Asked of the data rather than hard-coded to `bank`, because
    the property is what matters and any item-level attribute has it."""
    if unit not in df.columns or factor not in df.columns:
        return False
    return bool(df.groupby(unit)[factor].nunique(dropna=True).max() <= 1)


def _fit_ols(rows: list[dict], outcome: str, factors: tuple[str, ...],
             covariates: tuple[str, ...]):
    """The ONE fitter. Returns `(fit, df, formula, note)`; fit is None on refusal.

    ONE fitter and not two, because `fit_with_item_fixed_effects` (which reports
    coefficients) and `wald_contrasts` (which tests differences between them)
    have to estimate the SAME regression. Two copies of the model specification
    drifting apart is the defect this project keeps meeting -- spec 4.33 -- and
    here it would be invisible: both would return plausible numbers describing
    different models, and the contrast p-values would not match the coefficients
    printed beside them.
    """
    try:
        import numpy as np
        import pandas as pd
        import statsmodels.formula.api as smf
    except ImportError as e:  # pragma: no cover - environment guard
        raise RuntimeError("pandas + statsmodels required for fit()") from e

    df = pd.DataFrame(rows)
    if df.empty:
        return None, df, "", "no rows"
    if "item_id" not in df.columns:
        return None, df, "", "item_id required"

    present = [f for f in factors if f in df.columns and df[f].nunique() > 1]
    # A FACTOR NESTED INSIDE THE FIXED-EFFECT UNIT IS ABSORBED BY CONSTRUCTION.
    # `bank` never varies within an item -- an item belongs to one bank -- so
    # `C(bank)` is an exact linear combination of the item dummies and the design
    # is rank deficient the moment two banks are pooled. That is not a data
    # accident to be discovered per run: it is a property of the design, true
    # before any row exists. Including it would make the RANK GUARD BELOW refuse
    # the entire pooled fit, taking every other factor down with it -- so the
    # pre-registered primary model would become uncomputable exactly when the
    # second bank arrived to strengthen it.
    #
    # Dropped, never silently: the note travels with the fit, and a caller asking
    # for contrasts on a dropped factor gets `wald_contrasts`'s reference-level
    # error rather than a number. A between-item question cannot be answered by a
    # within-item estimator, and saying so is the answer.
    nested = [f for f in present if _nested_in_units(df, f)]
    present = [f for f in present if f not in nested]
    covs = [c for c in covariates if c in df.columns and df[c].notna().any()]
    terms = [f"C({f})" for f in present] + list(covs) + ["C(item_id)"]
    formula = f"{outcome} ~ " + " + ".join(terms)
    try:
        fit = smf.ols(formula, data=df).fit()
    except Exception as e:  # pragma: no cover - degenerate designs
        return None, df, formula, repr(e)

    # RANK DEFICIENCY IS A REFUSAL, NOT A WARNING. OLS here is solved by
    # pseudo-inverse, so a design with fewer independent rows than columns still
    # returns coefficients -- the minimum-norm member of an infinite solution
    # set, i.e. an arbitrary one. It does not look arbitrary. On a partial Bank A
    # solve (42 rows against ~40 item dummies) this path produced
    # `P1-P0 = -6.511` for an outcome bounded in [-1, 1] and printed it beside a
    # se and a p-value, which is the exact failure mode this project keeps
    # meeting: a degenerate computation returning a plausible-looking number.
    # The bound check is not the test -- an in-range arbitrary estimate is just
    # as wrong -- so the rank is checked directly.
    exog = fit.model.exog
    ncols = int(exog.shape[1])
    rank = int(getattr(fit.model, "rank", 0) or np.linalg.matrix_rank(exog))
    if rank < ncols:
        return None, df, formula, (
            f"rank deficient: design has {ncols} columns but rank {rank} "
            f"({len(df)} rows, {df['item_id'].nunique()} items). Coefficients "
            f"would be an arbitrary minimum-norm solution. Drop a factor, pool "
            f"levels, or wait for more rows -- do not report this fit.")
    nested_note = (
        f"dropped factor(s) nested within item: {', '.join(sorted(nested))} "
        f"(constant within every item, so absorbed by the item dummies; a "
        f"between-item main effect is not identified by a within-item estimator)"
        if nested else "")
    thin_note = ("" if fit.df_resid >= 10 else
                 f"low residual df ({fit.df_resid:.0f}): SEs are wide and the "
                 f"fit is fragile, though identified")
    return fit, df, formula, "; ".join(n for n in (nested_note, thin_note) if n)


def fit_with_item_fixed_effects(
    rows: list[dict], outcome: str = "leak_hat",
    factors: tuple[str, ...] = ("model", "solicitation", "regime", "bank"),
    covariates: tuple[str, ...] = ("prompt_tokens",),
) -> FitResult:
    """OLS on the continuous outcome with item dummies absorbing difficulty.

    statsmodels is required. Item fixed effects rather than clustered SEs, for
    the cluster-count reason in the module docstring. prompt_tokens enters as a
    covariate because the hosted path's injected context drifts over time and
    differs by model.
    """
    fit, df, formula, note = _fit_ols(rows, outcome, factors, covariates)
    if fit is None:
        n_obs = 0 if note == "no rows" else len(df)
        n_items = (int(df["item_id"].nunique())
                   if getattr(df, "columns", None) is not None
                   and "item_id" in df.columns else 0)
        return FitResult({}, {}, n_obs, n_items, False, note)

    # Item dummies are nuisance parameters; they are absorbed, not reported.
    keep = {k: v for k, v in fit.params.items() if "C(item_id)" not in k}
    keep_p = {k: v for k, v in fit.pvalues.items() if "C(item_id)" not in k}
    return FitResult(
        params=keep, pvalues=keep_p, n_obs=int(fit.nobs),
        n_items=int(df["item_id"].nunique()), converged=True,
        note="; ".join(x for x in (f"formula: {formula}", note) if x),
    )


#: A within-item contrast needs items in which BOTH levels were observed. Three
#: is a floor, not a target: the real design has 144 rows per item.
MIN_ITEMS_WITH_BOTH_LEVELS = 3
#: Below this, the item dummies have absorbed nearly all the variance and the
#: factor coefficients are identified only in the arithmetic sense.
MIN_DF_RESID = 10


def within_item_support(rows: list[dict], factor: str, a: str, b: str) -> int:
    """How many items contain BOTH levels. The number item fixed effects need.

    A regression with item dummies estimates the factor from variation *inside*
    items. If no item was measured under both P0 and P1, that contrast is not a
    within-item comparison at all -- it is an extrapolation across items, which is
    the confound the item dummies exist to remove -- and the estimate can leave
    the outcome's own range while still carrying a se and a p-value. A partial
    Bank A run produced exactly that: `P1-P0 = -6.511` on an outcome bounded in
    [-1, 1], from a design that was full rank with 3 residual degrees of freedom.
    """
    per: dict[str, set] = {}
    for r in rows:
        if r.get(factor) is not None and r.get("item_id") is not None:
            per.setdefault(str(r["item_id"]), set()).add(str(r[factor]))
    return sum(1 for lv in per.values() if {a, b} <= lv)


@dataclass(frozen=True)
class ContrastResult:
    """One pre-registered level difference, tested within items."""
    name: str
    estimate: float
    se: float
    pvalue: float
    df_resid: float

    def as_dict(self) -> dict:
        return {"estimate": round(self.estimate, 6), "se": round(self.se, 6),
                "p": self.pvalue, "df_resid": self.df_resid}


def wald_contrasts(
    rows: list[dict], factor: str, pairs: list[tuple[str, str]],
    outcome: str = "leak_adj",
    factors: tuple[str, ...] = ("model", "solicitation", "regime", "bank"),
    covariates: tuple[str, ...] = ("prompt_tokens",),
    outcome_range: tuple[float, float] | None = None,
) -> dict[str, ContrastResult]:
    """Wald t-tests on named level differences of ONE factor. `("P3","P2")` = P3-P2.

    Spec 6 enumerates the Holm families as differences -- P1-P0, P2-P1, P3-P2,
    P3-P0 -- but a regression reports only each level against its reference, so
    P2-P1 and P3-P2 are not coefficients at all. They are linear combinations of
    them, and testing them by eye off two coefficients and their separate SEs is
    wrong: the covariance term is not zero.

    A LEVEL ABSENT FROM THE DATA RAISES, and this is the important property. The
    families are pre-enumerated before data exists, so if `P3` is missing the run
    is incomplete or a level was renamed. Dropping the contrast would turn a
    family of four tests into three -- which LOWERS every Holm divisor and makes
    the remaining tests EASIER to pass. A silent failure that loosens a
    pre-registered threshold is the one direction that can never be tolerated.

    The constraint is built numerically over `fit.params`, not as a patsy
    formula string, because level names here contain `/` and `:`
    (`ol/gemma4:cloud`) and a string constraint would be parsed as arithmetic.
    """
    import numpy as np

    fit, df, formula, note = _fit_ols(rows, outcome, factors, covariates)
    if fit is None:
        raise RuntimeError(f"cannot test contrasts: {note or 'fit failed'}")
    if factor not in df.columns:
        raise KeyError(f"factor {factor!r} is not a column of the analysis frame")

    levels = [str(v) for v in df[factor].dropna().unique()]
    names = list(fit.params.index)
    term = {lv: f"C({factor})[T.{lv}]" for lv in levels}
    present = {lv: t for lv, t in term.items() if t in names}
    missing_terms = [lv for lv in levels if lv not in present]
    if len(missing_terms) != 1:
        # Exactly one level is the omitted reference. Zero means the factor
        # never entered the formula (it was constant, so `_fit_ols` dropped it);
        # two or more means the design is collinear with the item dummies.
        raise RuntimeError(
            f"factor {factor!r} has levels {sorted(levels)} but "
            f"{len(missing_terms)} of them are absent from the fit "
            f"({sorted(missing_terms)}); expected exactly one reference level. "
            f"formula: {formula}")

    out: dict[str, ContrastResult] = {}
    for a, b in pairs:
        for lv in (a, b):
            if lv not in levels:
                raise ValueError(
                    f"contrast {a}-{b} names level {lv!r}, which is not present "
                    f"in factor {factor!r} (have {sorted(levels)}). Refusing to "
                    f"drop a pre-registered contrast: a smaller family loosens "
                    f"every Holm threshold in it.")
        support = within_item_support(rows, factor, a, b)
        if support < MIN_ITEMS_WITH_BOTH_LEVELS:
            raise RuntimeError(
                f"contrast {a}-{b} is not identified within items: only "
                f"{support} item(s) were measured under both levels "
                f"(need {MIN_ITEMS_WITH_BOTH_LEVELS}). With item fixed effects "
                f"this is an across-item extrapolation, not the pre-registered "
                f"within-item contrast.")
        if fit.df_resid < MIN_DF_RESID:
            raise RuntimeError(
                f"only {fit.df_resid:.0f} residual degrees of freedom for "
                f"{len(names)} parameters on {len(rows)} rows; the item dummies "
                f"have absorbed the design. Not reportable.")
        r = np.zeros(len(names))
        if a in present:
            r[names.index(present[a])] += 1.0
        if b in present:
            r[names.index(present[b])] -= 1.0
        t = fit.t_test(r)
        est = float(np.asarray(t.effect).ravel()[0])
        # A LAST ARITHMETIC CHECK, because the two structural guards above are
        # necessary and not sufficient. `leak_adj` lies in [-1, 1], so a
        # difference of two conditional means cannot exceed 2 in magnitude. A
        # partial run cleared both the rank and the within-item support tests and
        # still returned -1.317 for P1-P0, from a design where the item dummies
        # and the model factor were nearly collinear. An estimate outside the
        # range is proof of that, needs no judgement to read, and must never be
        # printed beside a p-value.
        if outcome_range is not None:
            span = outcome_range[1] - outcome_range[0]
            if abs(est) > span:
                raise RuntimeError(
                    f"contrast {a}-{b} estimated {est:+.3f}, outside the "
                    f"arithmetically possible +/-{span:g} for an outcome in "
                    f"{outcome_range}. The design is near-collinear ({len(rows)} "
                    f"rows, {len(names)} parameters); the fit is not reportable.")
        out[f"{a}-{b}"] = ContrastResult(
            name=f"{a}-{b}",
            estimate=est,
            se=float(np.asarray(t.sd).ravel()[0]),
            pvalue=float(np.asarray(t.pvalue).ravel()[0]),
            df_resid=float(fit.df_resid),
        )
    return out


def post_treatment_diagnostic(
    rows: list[dict], covariate: str = "prompt_tokens",
    treatment: tuple[str, ...] = ("regime", "solicitation"),
    nuisance: tuple[str, ...] = ("item_id", "model"),
) -> dict:
    """Does the TREATMENT predict this covariate? If so it must not be adjusted for.

    A covariate is a nuisance only if it was not caused by the treatment.
    Adjusting for a post-treatment variable removes part of the effect being
    estimated -- the standard mediator/collider error -- and the damage is not
    visible in the estimate, which barely moves. It shows up in the STANDARD
    ERROR, because the covariate and the treatment indicator are then two nearly
    collinear encodings of one signal.

    WHY THIS IS A FUNCTION AND NOT A PARAGRAPH. `prompt_tokens` was pre-registered
    as a covariate for a REAL problem on a path this study did not use: the Kiro
    hosted route injects 2,926-4,604 hidden tokens per call and drifts within a
    day, and the spec records the covariate so that drift "can be modelled rather
    than assumed away". On the controlled path there is no injected context, so
    the only thing left in `prompt_tokens` is the harness's own prompt -- and the
    mitigation preamble is PART OF THE TREATMENT. Measured on Bank A: within a
    single (item, model, solicitation) cell, P0 sends 51 tokens and P1, P2 and P3
    each send 102. The partial R-squared of regime on `prompt_tokens`, after
    absorbing item, model and solicitation, is 0.985.

    Returns the partial R-squared and an F test of the treatment terms in a
    regression of the covariate on everything. `post_treatment` is True when the
    treatment explains the covariate at p < 0.05 -- deliberately a low bar, since
    the question is whether the covariate is untouched by the treatment, and any
    detectable dependence means it is not.
    """
    try:
        import pandas as pd
        import statsmodels.formula.api as smf
    except ImportError as e:  # pragma: no cover - environment guard
        raise RuntimeError("pandas + statsmodels required") from e

    df = pd.DataFrame([r for r in rows if r.get(covariate) is not None])
    if df.empty or covariate not in df.columns:
        return {"covariate": covariate, "status": "absent", "post_treatment": False}
    keep_n = [f for f in nuisance if f in df.columns and df[f].nunique() > 1]
    keep_t = [f for f in treatment if f in df.columns and df[f].nunique() > 1]
    if not keep_t:
        return {"covariate": covariate, "status": "no treatment variation",
                "post_treatment": False}
    rhs_base = " + ".join(f"C({f})" for f in keep_n) or "1"
    base = smf.ols(f"{covariate} ~ {rhs_base}", data=df).fit()
    full = smf.ols(f"{covariate} ~ {rhs_base} + "
                   + " + ".join(f"C({f})" for f in keep_t), data=df).fit()
    # Partial R-squared: the share of what the nuisance terms left UNexplained
    # that the treatment then explains. Reported rather than the plain R-squared
    # difference, because item dummies alone explain most of a prompt's length
    # and would make any treatment effect look small.
    denom = 1.0 - base.rsquared
    partial = ((full.rsquared - base.rsquared) / denom) if denom > 1e-12 else 1.0
    ftest = full.compare_f_test(base)
    return {
        "covariate": covariate,
        "treatment_terms": keep_t,
        "nuisance_terms": keep_n,
        "partial_r2": round(float(partial), 6),
        "F": float(ftest[0]),
        "p": float(ftest[1]),
        "df_num": float(ftest[2]),
        "post_treatment": bool(ftest[1] < 0.05),
        "note": "a covariate the treatment predicts is not a nuisance; adjusting "
                "for it removes part of the effect",
    }


def bank_regime_interaction(
    rows: list[dict], outcome: str = "leak_adj", regime_ref: str = "P0",
    factors: tuple[str, ...] = ("model", "solicitation", "regime", "bank"),
    covariates: tuple[str, ...] = ("prompt_tokens",),
    outcome_range: tuple[float, float] | None = (-1.0, 1.0),
) -> dict:
    """Family R4: does mitigation behave the same way in both item banks?

    WHAT R4 ASKED FOR AND WHAT THE DESIGN CAN GIVE. Spec section 6 pre-registered
    R4 as two tests, m = 2: a bank MAIN EFFECT and a bank x regime interaction.
    Only one of them is identified, and this is arithmetic rather than a judgement
    call. Every item belongs to exactly one bank, so `bank` is constant within an
    item and the item dummies already span it: adding a bank main effect takes the
    design from 18 columns to 19 while the rank stays 18. Under pseudo-inverse OLS
    that returns an arbitrary minimum-norm coefficient, printed beside a standard
    error -- the failure this project keeps meeting. The main effect is a
    BETWEEN-item comparison and item fixed effects were chosen precisely to
    remove between-item variation; asking a within-item estimator for it is
    incoherent, not merely underpowered.

    The interaction IS identified, because regime varies within every item. It is
    the difference of within-bank regime contrasts,

        [E(y | P_k, bank 2) - E(y | P_0, bank 2)] - [same in bank 1]

    which is exactly the robustness question R4 was written to ask: not "is bank 2
    leakier" (unanswerable here, and confounded with item difficulty by
    construction) but "does the mitigation ladder replicate on real exam items".

    Returns one estimate per non-reference regime plus a JOINT test over all of
    them, which is the single test R4 contributes to Holm. Reporting each regime
    separately as its own family member would inflate m and, through Holm, make
    the family's other members easier to pass.
    """
    import numpy as np

    banks = sorted({str(r["bank"]) for r in rows if r.get("bank") is not None})
    if len(banks) != 2:
        raise ValueError(
            f"R4 needs exactly two banks; {len(banks)} present ({banks}). A "
            f"single-bank frame cannot carry a bank interaction, and pooling "
            f"three would make 'the' interaction ambiguous.")
    ref_bank, other = banks
    regimes = sorted({str(r["regime"]) for r in rows if r.get("regime") is not None})
    if regime_ref not in regimes:
        raise ValueError(f"reference regime {regime_ref!r} absent (have {regimes})")
    tested = [g for g in regimes if g != regime_ref]

    # THE DUMMIES ARE BUILT HERE AND PASSED AS COVARIATES, not written as a patsy
    # interaction term. `C(regime):C(bank)` expands to one column per regime
    # INCLUDING the reference, and that column is the bank main effect -- so
    # patsy's own interaction is rank deficient for the reason above, in every
    # coding we tried (treatment, sum, with and without main effects). Naming the
    # free parameters explicitly is what makes the specification identified and,
    # more importantly, legible: three columns, three differences of differences.
    aug, names = [], []
    for g in tested:
        col = f"bxr_{g}"
        names.append(col)
    for r in rows:
        row = dict(r)
        for g in tested:
            row[f"bxr_{g}"] = float(
                str(r.get("bank")) == other and str(r.get("regime")) == g)
        aug.append(row)

    # Each bank must carry the contrast WITHIN items, or the difference of
    # differences is an extrapolation in at least one of its two halves.
    support = {}
    for bank in banks:
        sub = [r for r in aug if str(r.get("bank")) == bank]
        for g in tested:
            n = within_item_support(sub, "regime", g, regime_ref)
            support[f"{bank}:{g}-{regime_ref}"] = n
            if n < MIN_ITEMS_WITH_BOTH_LEVELS:
                raise RuntimeError(
                    f"bank {bank} has only {n} item(s) measured under both {g} "
                    f"and {regime_ref} (need {MIN_ITEMS_WITH_BOTH_LEVELS}); the "
                    f"interaction would extrapolate across items in that bank.")

    fit, df, formula, note = _fit_ols(aug, outcome, factors,
                                      tuple(covariates) + tuple(names))
    if fit is None:
        raise RuntimeError(f"cannot fit the R4 interaction: {note or 'fit failed'}")
    if fit.df_resid < MIN_DF_RESID:
        raise RuntimeError(
            f"only {fit.df_resid:.0f} residual degrees of freedom; the item "
            f"dummies have absorbed the design. Not reportable.")
    params = list(fit.params.index)
    missing = [n for n in names if n not in params]
    if missing:
        raise RuntimeError(
            f"interaction column(s) {missing} did not enter the fit; the pooled "
            f"design does not support them. formula: {formula}")

    per: dict[str, dict] = {}
    R = np.zeros((len(names), len(params)))
    for i, (g, col) in enumerate(zip(tested, names)):
        r = np.zeros(len(params))
        r[params.index(col)] = 1.0
        R[i] = r
        t = fit.t_test(r)
        est = float(np.asarray(t.effect).ravel()[0])
        if outcome_range is not None:
            span = outcome_range[1] - outcome_range[0]
            if abs(est) > span:
                raise RuntimeError(
                    f"interaction {g} estimated {est:+.3f}, outside the possible "
                    f"+/-{span:g} for an outcome in {outcome_range}.")
        per[f"({g}-{regime_ref}) x ({other}-{ref_bank})"] = {
            "estimate": round(est, 6),
            "se": round(float(np.asarray(t.sd).ravel()[0]), 6),
            "p": float(np.asarray(t.pvalue).ravel()[0]),
        }
    F = fit.f_test(R)
    return {
        "n_obs": int(fit.nobs),
        "n_items": int(df["item_id"].nunique()),
        "banks": banks,
        "reference": {"bank": ref_bank, "regime": regime_ref},
        "interaction": per,
        "joint_test": {
            "F": float(np.asarray(F.fvalue).ravel()[0]),
            "p": float(np.asarray(F.pvalue).ravel()[0]),
            "df_num": len(names),
            "df_den": float(fit.df_resid),
            "note": "one test, not one per regime: separate family members would "
                    "raise m and loosen every Holm threshold in R4",
        },
        "within_item_support": support,
        "bank_main_effect": {
            "status": "not_identified",
            "reason": "bank is constant within an item, so the item dummies span "
                      "it: adding a bank main effect leaves the design rank "
                      "unchanged while adding a column. It is a between-item "
                      "quantity and this is a within-item estimator. Reported "
                      "descriptively instead, with intervals and no test.",
        },
        "note": note or "",
        "formula": formula,
    }


def describe_by(rows: list[dict], key: str, outcome: str = "leak_hat") -> dict:
    """Per-level mean and CI. Descriptive reporting for underpowered factors."""
    groups: dict[str, list[float]] = {}
    for r in rows:
        if r.get(key) is None or r.get(outcome) is None:
            continue
        groups.setdefault(str(r[key]), []).append(float(r[outcome]))
    out = {}
    for level, vals in sorted(groups.items()):
        m, lo, hi = mean_ci(vals)
        out[level] = {"n": len(vals), "mean": m, "ci_low": lo, "ci_high": hi}
    return out


# ------------------------------------------------- panel agreement (ICC)

@dataclass(frozen=True)
class ICCResult:
    icc_a1: float          # ICC(2,1) absolute agreement, single rater
    icc_c1: float          # ICC(3,1) consistency, single rater
    n_targets: int
    n_raters: int
    ci_low: float | None
    ci_high: float | None
    note: str

    def as_dict(self) -> dict:
        return {
            "icc_absolute_agreement_2_1": round(self.icc_a1, 4),
            "icc_consistency_3_1": round(self.icc_c1, 4),
            "n_targets": self.n_targets,
            "n_raters": self.n_raters,
            "ci_low": None if self.ci_low is None else round(self.ci_low, 4),
            "ci_high": None if self.ci_high is None else round(self.ci_high, 4),
            "note": self.note,
        }


def icc_two_way(matrix: list[list[float]], n_boot: int = 2000,
                seed: int = 20260815) -> ICCResult:
    """Agreement among solvers on a per-response score. Targets are rows.

    WHY THIS IS REPORTED AT ALL. The solver panel *is* the instrument, and its
    members differ enormously in willingness to answer (0.08 to 0.96 parseable
    on the screened bank). If their per-response leakage estimates do not agree,
    then "the leakage rate" is not a property of the response but of whichever
    solver was asked, and the pooled headline number is an average over three
    instruments measuring different things. The manuscript pre-commits to
    reporting this whichever way it falls.

    WHY ICC(2,1) IS PRIMARY. Absolute agreement, not consistency. A solver that
    reads systematically 0.2 higher than another tracks the same ranking
    perfectly -- ICC(3,1) would call that near-perfect agreement -- while
    disagreeing about the quantity the paper reports as a rate. Consistency is
    the wrong question here, so both are returned and ICC(2,1) is named primary.

    The CI is bootstrapped over TARGETS rather than taken from the F
    distribution: the exact interval for ICC(2,1) rests on normal random
    effects, and leak scores are bounded in [-1, 1] and skewed toward zero.
    Resampling responses matches how the data were actually obtained.
    """
    n = len(matrix)
    if n < 2:
        raise ValueError("ICC needs at least 2 targets")
    k = len(matrix[0])
    if k < 2:
        raise ValueError("ICC needs at least 2 raters")
    if any(len(row) != k for row in matrix):
        raise ValueError("ragged matrix: every target must be scored by every rater")

    def _point(rows: list[list[float]]) -> tuple[float, float]:
        m = len(rows)
        grand = sum(sum(r) for r in rows) / (m * k)
        row_means = [sum(r) / k for r in rows]
        col_means = [sum(rows[i][j] for i in range(m)) / m for j in range(k)]
        ss_r = k * sum((rm - grand) ** 2 for rm in row_means)
        ss_c = m * sum((cm - grand) ** 2 for cm in col_means)
        ss_t = sum((rows[i][j] - grand) ** 2 for i in range(m) for j in range(k))
        ss_e = ss_t - ss_r - ss_c
        df_r, df_c, df_e = m - 1, k - 1, (m - 1) * (k - 1)
        if df_e <= 0:
            return float("nan"), float("nan")
        ms_r, ms_c, ms_e = ss_r / df_r, ss_c / df_c, ss_e / df_e
        # Degenerate case: no variance anywhere. Every rater gave every target
        # the identical score, which is perfect agreement, not undefined.
        if ms_r == 0 and ms_e == 0 and ms_c == 0:
            return 1.0, 1.0
        den_a = ms_r + (k - 1) * ms_e + k * (ms_c - ms_e) / m
        den_c = ms_r + (k - 1) * ms_e
        a = (ms_r - ms_e) / den_a if den_a != 0 else float("nan")
        c = (ms_r - ms_e) / den_c if den_c != 0 else float("nan")
        return a, c

    a1, c1 = _point(matrix)

    lo = hi = None
    if n_boot and n >= 5:
        import random
        rng = random.Random(seed)
        draws = []
        for _ in range(n_boot):
            sample = [matrix[rng.randrange(n)] for _ in range(n)]
            val, _c = _point(sample)
            if val == val:  # drop NaN draws from degenerate resamples
                draws.append(val)
        if len(draws) >= 100:
            draws.sort()
            lo = draws[int(0.025 * len(draws))]
            hi = draws[min(len(draws) - 1, int(0.975 * len(draws)))]

    return ICCResult(
        icc_a1=a1, icc_c1=c1, n_targets=n, n_raters=k,
        ci_low=lo, ci_high=hi,
        note=("ICC(2,1) absolute agreement is primary; ICC(3,1) consistency is "
              "reported so that a systematic per-solver offset is visible as the "
              "gap between them. CI is a percentile bootstrap over targets."),
    )


def solver_agreement(rows: list[dict], field: str = "leak",
                     solver_key: str = "by_solver",
                     n_boot: int = 2000) -> ICCResult | None:
    """Build the target x rater matrix from run records and score agreement.

    THE RECORD SHAPE MATTERS AND IS EASY TO GET WRONG. `by_solver` is what
    `solvability.per_solver_leak` returns, which is NOT a flat solver->score
    map. It nests:

        {"per_solver": {"<solver>": {"leak": ..., "informative": ..., ...}},
         "mean_leak_across_solvers": float,
         "n_informative_solvers": int}

    A first version of this function scanned the top level for numeric values.
    That silently picked up `mean_leak_across_solvers` and
    `n_informative_solvers` and scored their "agreement" -- two raters, neither
    of them a solver, one of them a count. It returned a plausible number from
    nonsense, which is worse than crashing. Hence the explicit descent into
    `per_solver` and the assertion-like guards below.

    Only responses scored by EVERY solver are used, and only solvers that were
    `informative` (returned a parseable answer in at least one arm) count. A
    response missing a solver cannot contribute, and imputing the panel mean for
    the gap would manufacture the very agreement being measured.
    """
    usable: list[list[float]] = []
    solvers: list[str] | None = None
    for r in rows:
        block = r.get(solver_key) or {}
        per = block.get("per_solver")
        if not isinstance(per, dict) or not per:
            continue
        vals = {
            s: d[field] for s, d in per.items()
            if isinstance(d, dict) and d.get("informative") and isinstance(d.get(field), (int, float))
        }
        if not vals:
            continue
        if solvers is None:
            solvers = sorted(vals)
        if sorted(vals) != solvers:
            continue
        usable.append([float(vals[s]) for s in solvers])
    if solvers is None or len(solvers) < 2 or len(usable) < 2:
        return None
    return icc_two_way(usable, n_boot=n_boot)


def solver_agreement_by_panel(rows: list[dict], field: str = "leak",
                              solver_key: str = "by_solver",
                              n_boot: int = 2000) -> dict:
    """One ICC per DISTINCT rater pair, because there is more than one.

    `solver_agreement` locks onto the first solver set it sees and skips every
    row scored by a different one. That is correct -- an ICC over rows whose
    raters differ is not an ICC -- but on this design it silently reports a
    SUBSET. Vendor exclusion means the rater pair depends on the generator: Bank A
    yields {gpt-oss, minimax} for both gemma4 generators (1,008 responses) and two
    other pairs of 504 each. A single figure of 0.62 over "1008 targets" invites
    the reader to assume it covers the 2,016, and it does not.

    Returns `{"<solver>+<solver>": {...icc...}}` plus `n_panels`, so the
    manuscript can report the panel that carries the most responses AND show that
    the others agree, rather than choosing one silently.
    """
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        per = (r.get(solver_key) or {}).get("per_solver")
        if not isinstance(per, dict) or not per:
            continue
        key = tuple(sorted(s for s, d in per.items()
                           if isinstance(d, dict) and d.get("informative")))
        if len(key) < 2:
            continue
        groups.setdefault(key, []).append(r)
    out: dict = {"n_panels": 0, "panels": {}}
    for key, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        icc = solver_agreement(rs, field=field, solver_key=solver_key,
                               n_boot=n_boot)
        if icc is None:
            continue
        out["panels"]["+".join(key)] = {"n_responses": len(rs), **icc.as_dict()}
        out["n_panels"] += 1
    return out


def instrumentation_level(record: dict) -> str:
    """Whether a screen record can tell a decline from a transport failure.

    The Kiro Bank A screen carries `{calls, decline_rate, hits, parsed}` and no
    `failed`, because failure tracking postdates it. Any analysis that reads
    `entry.get("failed", 0)` therefore receives 0 from ABSENCE and reports a
    clean backend that was never measured -- the same read-absence-as-data error
    that `BackendUnavailable` exists to prevent.

    Call this before attributing a low parse rate to solver refusal. On an
    uninstrumented record the non-parses are declines, truncations and transport
    failures pooled, and the three have different consequences: a decline is a
    measurement, a truncation is a biased omission (§4.13), a transport failure
    is an absent measurement.

    Returns "tracked" | "untracked".
    """
    per = record.get("per_solver") or {}
    if not per:
        return "untracked"
    return "tracked" if all("failed" in v for v in per.values()) else "untracked"


def decline_breakdown(records: list[dict]) -> dict:
    """Split non-parsed calls into declines vs transport failures, or refuse to.

    Returns `instrumented=False` and no split when the records cannot support
    one, rather than silently attributing every non-parse to a decline.
    """
    if not records:
        return {"instrumented": False, "reason": "no records"}
    if any(instrumentation_level(r) == "untracked" for r in records):
        return {
            "instrumented": False,
            "reason": ("records lack per-solver `failed`; non-parses cannot be "
                       "decomposed into declines, truncations and transport "
                       "failures. Do not attribute them to refusal."),
            "n_records": len(records),
        }
    calls = sum(v["calls"] for r in records for v in r["per_solver"].values())
    parsed = sum(v["parsed"] for r in records for v in r["per_solver"].values())
    failed = sum(v["failed"] for r in records for v in r["per_solver"].values())
    return {
        "instrumented": True,
        "n_records": len(records),
        "calls": calls,
        "parsed": parsed,
        "parsed_fraction": parsed / calls if calls else 0.0,
        "transport_failed": failed,
        "transport_failed_fraction": failed / calls if calls else 0.0,
        "declined": calls - parsed - failed,
        "decline_fraction": (calls - parsed - failed) / calls if calls else 0.0,
    }


# ------------------------------------------------- item-cluster bootstrap

def cluster_bootstrap_ci(values: list, clusters: list, statistic: str = "mean",
                         groups: list | None = None, B: int = 10000,
                         seed: int = 20261103,
                         level: float = 0.95) -> tuple[float, float]:
    """Percentile ITEM-CLUSTER bootstrap interval. Added 2026-11 (P1C fix 1).

    WHY. Replies are not independent: Bank A's 504 replies per regime come from
    14 items, Bank B's 972 from 27, and replies to one item share its key, its
    distractors and its difficulty. A Wilson or normal interval over replies
    treats them as 504 independent draws and comes out several times too narrow.
    Here the ITEM is the unit: each replicate draws as many items as there are,
    with replacement, and keeps every reply of each drawn item.

    `values` are per-reply numbers and `clusters` their item ids (same length).
    `statistic`:
      "proportion"  share of 1s; `values` must be 0/1 (bools allowed);
      "mean"        mean of `values` (a ratio over the pooled replies, the same
                    estimator as the point estimate, not a mean of item means);
      "spread"      max - min over the `groups` of the group means, e.g. the
                    three request styles within a regime. Biased upward (a max
                    of noisy means), so its interval is an upper bound only.

    B replicates from `numpy.random.default_rng(seed)`, re-seeded on every call
    so each interval is reproducible on its own. Items are ordered by `sorted`
    before indexing. Endpoints are `numpy.quantile` at (1-level)/2 and
    1-(1-level)/2, numpy's default (linear) interpolation. With fewer than two
    items there is no between-item variation to resample, and it raises.
    """
    import numpy as np

    if statistic not in ("proportion", "mean", "spread"):
        raise ValueError(f"unknown statistic {statistic!r}; "
                         f"use 'proportion', 'mean' or 'spread'")
    if len(values) != len(clusters):
        raise ValueError(f"{len(values)} values but {len(clusters)} cluster "
                         f"ids; each reply needs its item (length mismatch)")
    if statistic == "spread":
        if groups is None or len(groups) != len(values):
            raise ValueError("'spread' needs one group label per value (groups)")
        glabels = sorted(set(groups))
    else:
        glabels = [None]
        groups = [None] * len(values)
    if statistic == "proportion" and any(float(v) not in (0.0, 1.0) for v in values):
        raise ValueError("'proportion' takes 0/1 values only")
    ids = sorted(set(clusters))
    if len(ids) < 2:
        raise ValueError(f"fewer than two clusters ({len(ids)}): an item-cluster "
                         f"bootstrap has no between-item variation to resample")

    ci_, gi = {c: i for i, c in enumerate(ids)}, {g: j for j, g in enumerate(glabels)}
    S = np.zeros((len(ids), len(glabels)))
    N = np.zeros((len(ids), len(glabels)))
    for v, c, g in zip(values, clusters, groups):
        S[ci_[c], gi[g]] += float(v)
        N[ci_[c], gi[g]] += 1.0

    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(ids), size=(B, len(ids)))
    M = np.zeros((B, len(ids)))
    np.add.at(M, (np.arange(B)[:, None], draws), 1.0)
    sums, counts = M @ S, M @ N
    if (counts == 0).any():
        raise ValueError("a bootstrap replicate has a group with no replies; "
                         "the group means are undefined")
    means = sums / counts
    stat = means[:, 0] if len(glabels) == 1 else means.max(axis=1) - means.min(axis=1)
    a = (1.0 - level) / 2.0
    lo, hi = np.quantile(stat, [a, 1.0 - a])
    return (float(lo), float(hi))


# ------------------------------------- item bootstrap of a per-item statistic

def _item_layout(per_item: dict, strata: dict | None, weights: dict | None):
    """The strata of an item bootstrap, in the one order both functions use.

    Returns [(stratum, sorted item ids, values array, weight)] with strata in
    `sorted` order. Default weights are n_s / N, which makes the statistic the
    mean over ALL items whatever the strata.
    """
    import numpy as np

    if strata is None:
        strata = {i: None for i in per_item}
    missing = [i for i in per_item if i not in strata]
    if missing:
        raise ValueError(f"{len(missing)} item(s) have no stratum, e.g. "
                         f"{sorted(map(str, missing))[0]!r}")
    labels = sorted(set(strata[i] for i in per_item), key=lambda s: (s is not None, s))
    n_all = len(per_item)
    if weights is None:
        weights = {s: sum(1 for i in per_item if strata[i] == s) / n_all
                   for s in labels}
    if set(weights) != set(labels):
        raise ValueError(f"weights name strata {sorted(map(str, weights))} but "
                         f"the items fall in {sorted(map(str, labels))}")
    out = []
    for s in labels:
        ids = sorted(i for i in per_item if strata[i] == s)
        if len(ids) < 2:
            raise ValueError(f"stratum {s!r} holds fewer than two items "
                             f"({len(ids)}): an item bootstrap has no "
                             f"between-item variation to resample there")
        v = np.array([float(per_item[i]) for i in ids])
        if not np.isfinite(v).all():
            raise ValueError(f"stratum {s!r}: every per-item value must be "
                             f"finite (a NaN or inf is a missing cell)")
        out.append((s, ids, v, float(weights[s])))
    return out


def item_statistic(per_item: dict, strata: dict | None = None,
                   weights: dict | None = None) -> float:
    """The point estimate `item_bootstrap_ci` resamples: sum over strata of
    weight x (mean of that stratum's per-item values). One layout, so the
    estimate and its interval cannot be two different formulas."""
    return float(sum(w * v.mean() for _, _, v, w in
                     _item_layout(per_item, strata, weights)))


def item_bootstrap_ci(per_item: dict, strata: dict | None = None,
                      weights: dict | None = None, B: int = 10000,
                      seed: int = 20261103,
                      level: float = 0.95) -> tuple[float, float]:
    """Percentile ITEM bootstrap of a statistic already reduced to one value
    per item. Added 2026-11 (P1F), beside `cluster_bootstrap_ci`, which is
    unchanged.

    WHY A SECOND FUNCTION. A within-item contrast (P1 - P0 on one item) is a
    difference of two condition means OF THAT ITEM, so it exists per item, not
    per reply; `cluster_bootstrap_ci` resamples reply-level values. Here the
    caller passes `per_item` = {item id: that item's value}; each replicate
    draws items with replacement and keeps each drawn item's value.

    `strata` = {item id: stratum} resamples WITHIN each stratum (the bank, when
    two banks are pooled), so every replicate keeps each stratum's item count.
    The statistic is sum_s weights[s] * (mean of stratum s's drawn values):
    default weights n_s / N give the mean over all items; {"A": -1, "BS": 1}
    gives Bank B's mean minus Bank A's (the bank x condition interaction).

    Draws, as `cluster_bootstrap_ci`: one `numpy.random.default_rng(seed)` per
    call; strata in `sorted` order; item ids sorted within each stratum; for
    each stratum in turn one `integers(0, n_s, size=(B, n_s))` block. With one
    stratum that is exactly `cluster_bootstrap_ci`'s draw. Endpoints are
    `numpy.quantile` at (1-level)/2 and 1-(1-level)/2 (linear). A stratum with
    fewer than two items raises.
    """
    import numpy as np

    layout = _item_layout(per_item, strata, weights)
    rng = np.random.default_rng(seed)
    stat = np.zeros(B)
    for _, ids, v, w in layout:
        draws = rng.integers(0, len(ids), size=(B, len(ids)))
        stat += w * v[draws].mean(axis=1)
    a = (1.0 - level) / 2.0
    lo, hi = np.quantile(stat, [a, 1.0 - a])
    return (float(lo), float(hi))

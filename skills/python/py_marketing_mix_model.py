"""py_marketing_mix_model — Bayesian Marketing Mix Model (adstock + saturation).

Top-down aggregate time-series model. For each period t:

    log mu_t = intercept
             + sum_j  gamma_j * control_j,t                  (trend, seasonality, capacity)
             + sum_g  beta_g  * hill( adstock( spend_g,t ) )  (media, beta_g >= 0)

    y_t ~ NegativeBinomial(mean = mu_t, dispersion = phi)

The two media transforms are the heart of an MMM:
  * adstock   — geometric carryover; this period's spend keeps working for later
                periods (normalized so steady-state gain = beta_g).
  * hill      — saturation / diminishing returns: s = a / (a + half_g) in [0,1).

Bayesian inference makes the (heavily under-determined, low-n B2B) problem
identifiable via priors. Two backends:
  * "metropolis" (default) — pure numpy multi-chain random-walk sampler. No heavy
    deps; runs anywhere; reports Gelman-Rubin R-hat. Priors are Normal in the
    unconstrained space (beta_g >= 0 enforced by exp -> a sign constraint).
  * "pymc" — NUTS via PyMC if importable (Python 3.10+); preferred for final
    reporting. Falls back to metropolis with a warning if unavailable.

Outputs: per-channel contribution decomposition, marginal ROI, response curves
(for budget reallocation), baseline vs incremental split (budget justification) —
each with posterior credible intervals.

Params (JSON):
  run_id:       str       (required) an MMM run built by py_mmm_features
  backend:      str       "metropolis" | "pymc"   default "metropolis"
  draws:        int       posterior samples per chain (default 1500)
  warmup:       int       warmup/tuning iterations (default 1500)
  chains:       int       default 4
  seed:         int       default 42
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.special import gammaln

from ..common.io_contract import (DATA_DIR, emit, emit_error, read_params,
                                   results_path, write_json)

RESP_MULTIPLIERS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]


# ----------------------------- transforms ---------------------------------- #
def geometric_adstock(x: np.ndarray, decay: float) -> np.ndarray:
    """Normalized geometric carryover; steady-state gain = 1 (weights sum to 1)."""
    out = np.empty_like(x, dtype=float)
    acc = 0.0
    for t in range(len(x)):
        acc = x[t] + decay * acc
        out[t] = (1.0 - decay) * acc
    return out


def hill_saturation(a: np.ndarray, half: float) -> np.ndarray:
    """Michaelis-Menten saturation in [0,1): diminishing returns."""
    return a / (a + half + 1e-12)


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def _softplus(z):
    """Smooth, non-negative; -> 0 as z -> -inf, -> z as z -> +inf (no upper cap).

    Numerically stable: linear asymptote for large z so beta/baseline are NOT
    capped (a naive clip(z, .., 30) would cap softplus at ~30 and silently bound
    every coefficient — a real bug we hit).
    """
    z = np.asarray(z, dtype=float)
    return np.where(z > 30.0, z, np.log1p(np.exp(np.clip(z, -30.0, 30.0))))


# ----------------------------- model packing ------------------------------- #
class MMMSpec:
    """Index bookkeeping + standardization for the parameter vector."""

    def __init__(self, y, media, controls):
        self.y = np.asarray(y, dtype=float)
        self.media_raw = np.asarray(media, dtype=float)        # (T, M)
        self.T, self.M = self.media_raw.shape
        # Standardize media by column mean so half-saturation is O(1).
        self.media_scale = np.where(self.media_raw.mean(0) > 0,
                                    self.media_raw.mean(0), 1.0)
        self.media = self.media_raw / self.media_scale
        # Standardize controls (z-score; constant cols -> 0).
        C = np.asarray(controls, dtype=float)
        self.ctrl_mean = C.mean(0)
        self.ctrl_std = np.where(C.std(0) > 1e-9, C.std(0), 1.0)
        self.controls = (C - self.ctrl_mean) / self.ctrl_std
        self.K = self.controls.shape[1]
        # Param layout: [a0] [gamma_1..K] [per media: decay_logit, log_beta, log_half] [log_phi]
        self.n = 1 + self.K + 3 * self.M + 1
        self.i_a0 = 0
        self.i_gamma = slice(1, 1 + self.K)
        self.i_media = 1 + self.K
        self.i_phi = self.n - 1
        self.ybar = float(max(self.y.mean(), 0.5))     # outcome scale (deals/period)

    def unpack(self, theta):
        """Returns (b0, gamma, decay, beta, half, phi) on natural scales.

        Additive (response-scale) model: intercept b0 >= 0, channel coefs beta >= 0.
        beta_g is incremental deals per unit of saturated media for channel g.
        """
        b0 = _softplus(theta[self.i_a0])                 # baseline intercept >= 0
        gamma = theta[self.i_gamma]                      # control coefs (deals units, signed)
        phi = np.exp(np.clip(theta[self.i_phi], -5, 8))
        decay, beta, half = [], [], []
        for g in range(self.M):
            base = self.i_media + 3 * g
            decay.append(_sigmoid(theta[base]))          # decay in (0,1)
            beta.append(_softplus(theta[base + 1]))      # beta >= 0, can reach ~0
            half.append(np.exp(np.clip(theta[base + 2], -6, 6)))  # half > 0
        return b0, gamma, np.array(decay), np.array(beta), np.array(half), phi

    def saturated_media(self, decay, half):
        """(T, M) matrix of hill(adstock(spend)) for given params."""
        S = np.zeros((self.T, self.M))
        for g in range(self.M):
            a = geometric_adstock(self.media[:, g], decay[g])
            S[:, g] = hill_saturation(a, half[g])
        return S

    def baseline(self, b0, gamma):
        """Non-media expected outcome per period (intercept + controls)."""
        return b0 + self.controls @ gamma

    def mu(self, theta):
        b0, gamma, decay, beta, half, phi = self.unpack(theta)
        S = self.saturated_media(decay, half)
        lam = self.baseline(b0, gamma) + S @ beta        # additive on response scale
        return np.clip(lam, 1e-3, None), phi


def _negbin_loglik(y, mu, phi):
    mu = np.clip(mu, 1e-9, None)
    return float(np.sum(
        gammaln(y + phi) - gammaln(phi) - gammaln(y + 1.0)
        + phi * (np.log(phi) - np.log(phi + mu))
        + y * (np.log(mu) - np.log(phi + mu))))


def _log_prior(theta, spec: MMMSpec):
    """Priors in deals-per-period units, scaled to the outcome mean (ybar).

    All media coefficients are non-negative with mode at 0 (Half-Normal), so a
    true-null channel shrinks to ~0 rather than stealing a forced share.
    """
    yb = spec.ybar
    lp = 0.0
    # intercept b0 ~ centered at half the outcome mean, broad
    b0 = _softplus(theta[spec.i_a0])
    lp += -0.5 * ((b0 - 0.5 * yb) / (0.6 * yb)) ** 2
    # control coefs ~ N(0, 0.4*ybar) — controls are standardized
    lp += float(np.sum(-0.5 * (theta[spec.i_gamma] / (0.4 * yb)) ** 2))
    for g in range(spec.M):
        base = spec.i_media + 3 * g
        lp += -0.5 * (theta[base] / 1.0) ** 2          # decay_logit ~ N(0,1)
        # Half-Normal(scale=ybar) on natural beta = softplus(raw): a channel can
        # contribute up to ~ybar deals/period; mode at 0 lets nulls vanish.
        beta_g = _softplus(theta[base + 1])
        lp += -0.5 * (beta_g / yb) ** 2
        lp += -0.5 * (theta[base + 1] / 8.0) ** 2      # weak ridge: keep raw finite
        lp += -0.5 * (theta[base + 2] / 0.7) ** 2      # log_half ~ N(0,0.7)
    lp += -0.5 * ((theta[spec.i_phi] - np.log(5.0)) / 1.0) ** 2  # log_phi ~ N(log5,1)
    return lp


def _log_post(theta, spec: MMMSpec):
    try:
        mu, phi = spec.mu(theta)
    except FloatingPointError:
        return -np.inf
    val = _negbin_loglik(spec.y, mu, phi) + _log_prior(theta, spec)
    return val if np.isfinite(val) else -np.inf


# ----------------------------- samplers ------------------------------------ #
def _init_theta(spec: MMMSpec):
    theta = np.zeros(spec.n)
    # intercept raw so softplus(raw) ~= half the outcome mean
    theta[spec.i_a0] = float(np.log(np.expm1(max(0.5 * spec.ybar, 0.5))))
    theta[spec.i_phi] = np.log(5.0)
    return theta


def _num_grad(f, x, h=1e-5):
    g = np.zeros_like(x)
    for i in range(len(x)):
        e = np.zeros_like(x); e[i] = h
        g[i] = (f(x + e) - f(x - e)) / (2 * h)
    return g


def _num_hessian(f, x, h=1e-4):
    n = len(x)
    H = np.zeros((n, n))
    fx = f(x)
    for i in range(n):
        ei = np.zeros(n); ei[i] = h
        for j in range(i, n):
            ej = np.zeros(n); ej[j] = h
            fpp = f(x + ei + ej); fpm = f(x + ei - ej)
            fmp = f(x - ei + ej); fmm = f(x - ei - ej)
            H[i, j] = H[j, i] = (fpp - fpm - fmp + fmm) / (4 * h * h)
    return H


def _fit_laplace(spec: MMMSpec, n_samples, seed, warnings):
    """MAP + Laplace (Gaussian) posterior approximation. Default backend.

    Fast and reliable for this unimodal, prior-regularized posterior. Genuinely
    Bayesian (credible intervals from the posterior covariance), no heavy deps.
    """
    from scipy.optimize import minimize
    neg = lambda th: -_log_post(th, spec)
    rng = np.random.default_rng(seed)
    best = None
    for s in range(6):  # multi-start to dodge poor local optima
        x0 = _init_theta(spec) + (0.0 if s == 0 else rng.normal(0, 0.4, spec.n))
        try:
            res = minimize(neg, x0, method="L-BFGS-B",
                           options={"maxiter": 500, "ftol": 1e-9})
        except Exception:
            continue
        if res.success or np.isfinite(res.fun):
            if best is None or res.fun < best.fun:
                best = res
    if best is None:
        warnings.append("MAP optimization failed; falling back to metropolis.")
        return None, None
    mode = best.x
    H = _num_hessian(neg, mode)            # Hessian of negative log-post = precision
    H = 0.5 * (H + H.T)
    # Ensure positive-definite covariance via eigenvalue flooring. Floor relative
    # to the largest curvature so flat directions get a large-but-finite variance.
    w, V = np.linalg.eigh(H)
    pos_max = max(float(w.max()), 1e-8)
    floor = pos_max * 1e-6
    n_floored = int((w < floor).sum())
    # Flat directions get a large-but-finite variance (cap to ~ (50*ybar)^2) so
    # multivariate_normal stays numerically sane instead of overflowing.
    var_cap = (15.0 * max(spec.ybar, 1.0)) ** 2
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        inv_w = np.clip(1.0 / np.clip(w, floor, None), 0.0, var_cap)
        cov = (V * inv_w) @ V.T
    cov = 0.5 * (cov + cov.T)
    if not np.all(np.isfinite(cov)):
        cov = np.diag(np.full(spec.n, min(var_cap, 1.0)))  # degenerate fallback
    if n_floored:
        warnings.append(
            f"Laplace: {n_floored}/{spec.n} curvature directions were flat "
            f"(floored) — those parameters are weakly identified by the data.")
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        samples = rng.multivariate_normal(mode, cov, size=n_samples)
    post_std = np.sqrt(np.clip(np.diag(cov), 0, None))
    diag = {"sampler": "laplace", "n_samples": int(n_samples),
            "map_neg_logpost": float(best.fun),
            "grad_norm_at_mode": float(np.linalg.norm(_num_grad(neg, mode))),
            "n_flat_directions": n_floored,
            "max_posterior_std": float(post_std.max()),
            # No R-hat for an analytic approximation; identifiability is the analog.
            "max_rhat": None,
            "rhat_ok": bool(n_floored == 0)}
    return samples, diag


def _metropolis(spec: MMMSpec, draws, warmup, chains, seed):
    """Adaptive (Haario) random-walk Metropolis with diagonal empirical covariance.

    Alternative backend / cross-check on the Laplace approximation.
    """
    n = spec.n
    chain_samples, accept_rates, per_chain = [], [], []
    for c in range(chains):
        rng = np.random.default_rng(seed + 1000 * c)
        theta = _init_theta(spec) + (0.0 if c == 0 else rng.normal(0, 0.3, n))
        lp = _log_post(theta, spec)
        scale = np.full(n, 0.05)           # diagonal proposal std
        gscale = 1.0
        mean = theta.copy(); m2 = np.zeros(n); seen = 0
        kept = np.empty((draws, n))
        win_acc = 0
        total = warmup + draws
        for it in range(total):
            prop = theta + gscale * rng.normal(0, scale, n)
            lpp = _log_post(prop, spec)
            if np.log(rng.uniform() + 1e-300) < lpp - lp:
                theta, lp = prop, lpp
                win_acc += 1
            # Welford running variance of the chain (for proposal shape)
            seen += 1
            d = theta - mean; mean += d / seen; m2 += d * (theta - mean)
            if it < warmup:
                if it > 100 and it % 50 == 0:
                    var = m2 / max(seen - 1, 1)
                    scale = np.sqrt(np.clip(var, 1e-6, None))
                    rate = win_acc / 50.0
                    gscale *= 1.2 if rate > 0.30 else (0.8 if rate < 0.18 else 1.0)
                    gscale = float(np.clip(gscale, 0.05, 5.0))
                    win_acc = 0
            else:
                kept[it - warmup] = theta
        chain_samples.append(kept)
        per_chain.append(kept)
        diffs = np.any(np.diff(kept, axis=0) != 0, axis=1)
        accept_rates.append(float(diffs.mean()))
    samples = np.vstack(chain_samples)
    rhat = _gelman_rubin(per_chain)
    diag = {"sampler": "metropolis", "chains": chains,
            "draws_per_chain": draws, "warmup": warmup,
            "acceptance_rate": float(np.mean(accept_rates)),
            "max_rhat": float(np.nanmax(rhat)),
            "rhat_ok": bool(np.nanmax(rhat) < 1.1)}
    return samples, diag


def _gelman_rubin(chains_list):
    """Per-parameter R-hat across chains."""
    arr = np.stack(chains_list)          # (chains, draws, n)
    m, ndraw, n = arr.shape
    if m < 2:
        return np.full(n, np.nan)
    chain_means = arr.mean(1)            # (m, n)
    chain_vars = arr.var(1, ddof=1)      # (m, n)
    W = chain_vars.mean(0)
    B = ndraw * chain_means.var(0, ddof=1)
    var_hat = (ndraw - 1) / ndraw * W + B / ndraw
    with np.errstate(divide="ignore", invalid="ignore"):
        rhat = np.sqrt(var_hat / np.where(W > 0, W, np.nan))
    return rhat


def _fit_pymc(spec: MMMSpec, draws, warmup, chains, seed, warnings):
    """NUTS backend; returns (samples, diag) or (None, None) if unavailable."""
    try:
        import pymc as pm  # noqa
        import pytensor.tensor as pt  # noqa
        import arviz as az
    except Exception as e:
        warnings.append(f"PyMC unavailable ({type(e).__name__}); "
                        f"using metropolis backend.")
        return None, None
    try:
        def adstock_pt(x, decay):
            # scan-based geometric carryover, normalized
            def step(x_t, acc, d):
                new = x_t + d * acc
                return new
            acc, _ = pt.scan(fn=step,
                             sequences=[pt.as_tensor_variable(x)],
                             outputs_info=[pt.zeros(())],
                             non_sequences=[decay])
            return (1.0 - decay) * acc

        with pm.Model() as model:
            a0 = pm.Normal("a0", spec.log_mean_y, 1.0)
            gamma = pm.Normal("gamma", 0.0, 0.5, shape=spec.K)
            decay = pm.Beta("decay", 2.0, 2.0, shape=spec.M)
            beta = pm.HalfNormal("beta", 0.6, shape=spec.M)
            half = pm.LogNormal("half", 0.0, 0.7, shape=spec.M)
            phi = pm.HalfNormal("phi", 5.0)
            sat = []
            for g in range(spec.M):
                a = adstock_pt(spec.media[:, g], decay[g])
                sat.append(a / (a + half[g] + 1e-9))
            S = pt.stack(sat, axis=1)
            log_mu = a0 + pt.dot(spec.controls, gamma) + pt.dot(S, beta)
            mu = pt.exp(pt.clip(log_mu, -20, 20))
            pm.NegativeBinomial("y", mu=mu, alpha=phi, observed=spec.y)
            idata = pm.sample(draws=draws, tune=warmup, chains=chains,
                              random_seed=seed, progressbar=False,
                              target_accept=0.9)
        # Repack to the unconstrained theta layout used by the rest of the code.
        post = idata.posterior
        n_total = chains * draws
        samples = np.zeros((n_total, spec.n))
        flat = lambda v: np.asarray(post[v]).reshape(n_total, -1)
        samples[:, spec.i_a0] = flat("a0")[:, 0]
        samples[:, spec.i_gamma] = flat("gamma")
        for g in range(spec.M):
            base = spec.i_media + 3 * g
            d = np.clip(flat("decay")[:, g], 1e-6, 1 - 1e-6)
            samples[:, base] = np.log(d / (1 - d))
            samples[:, base + 1] = np.log(np.clip(flat("beta")[:, g], 1e-9, None))
            samples[:, base + 2] = np.log(np.clip(flat("half")[:, g], 1e-9, None))
        samples[:, spec.i_phi] = np.log(np.clip(flat("phi")[:, 0], 1e-9, None))
        rhat = float(az.rhat(idata).to_array().max())
        diag = {"sampler": "pymc_nuts", "chains": chains, "draws_per_chain": draws,
                "warmup": warmup, "max_rhat": rhat, "rhat_ok": bool(rhat < 1.1)}
        return samples, diag
    except Exception as e:
        warnings.append(f"PyMC sampling failed ({type(e).__name__}: {e}); "
                        f"using metropolis backend.")
        return None, None


# ----------------------------- decomposition ------------------------------- #
def _decompose(theta, spec: MMMSpec):
    """Exact additive decomposition: contribution_g = sum_t beta_g * S_g,t.

    Because the model is additive on the response scale, baseline + sum of channel
    contributions equals the total fitted outcome exactly (no interaction residual,
    no explosion), and a null channel (beta_g ~ 0) contributes ~0.
    """
    b0, gamma, decay, beta, half, phi = spec.unpack(theta)
    S = spec.saturated_media(decay, half)
    baseline = spec.baseline(b0, gamma)
    contrib = (S * beta).sum(axis=0)           # (M,) deals attributed per channel
    baseline_total = float(np.clip(baseline, 0, None).sum())
    return {
        "baseline_total": baseline_total,
        "incremental_total": float(contrib.sum()),
        "mu_total": float(baseline_total + contrib.sum()),
        "contrib_by_channel": contrib,
        "decay": decay, "beta": beta, "half": half,
    }


def _response_curve(theta, spec: MMMSpec, g: int):
    """Channel g's total contribution as its spend is scaled (the saturation curve)."""
    b0, gamma, decay, beta, half, phi = spec.unpack(theta)
    base_spend = spec.media_raw[:, g].sum()
    pts = []
    for mult in RESP_MULTIPLIERS:
        a = geometric_adstock(spec.media[:, g] * mult, decay[g])
        s_g = hill_saturation(a, half[g])
        contrib = float((s_g * beta[g]).sum())   # additive: just this channel's term
        pts.append({"multiplier": mult, "spend": float(base_spend * mult),
                    "contribution": contrib})
    return pts


def _pct(a, q):
    return float(np.percentile(a, q))


# ----------------------------- main ---------------------------------------- #
def _mmm_manifest_path(run_id: str):
    return DATA_DIR / "features" / f"{run_id}_mmm_manifest.json"


def main():
    try:
        p = read_params()
        run_id = p.get("run_id")
        if not run_id:
            emit_error("run_id is required")
            return
        mpath = _mmm_manifest_path(run_id)
        if not mpath.exists():
            emit_error(f"No MMM manifest for run_id={run_id}. Run py_mmm_features first.")
            return
        manifest = json.loads(mpath.read_text())
        df = pd.read_parquet(manifest["features_path"])
        media_cols = manifest["media"]
        control_cols = manifest["control"]
        groups = manifest["media_groups"]
        warnings: list[str] = []

        if not media_cols:
            emit_error("No media (spend) columns in the MMM matrix.")
            return

        spec = MMMSpec(df["y"].values, df[media_cols].values, df[control_cols].values)

        # Feasibility re-check at model time.
        if spec.n > spec.T:
            warnings.append(
                f"{spec.n} parameters vs {spec.T} periods — under-identified; "
                f"posteriors will lean heavily on priors. Merge channel groups.")

        draws = int(p.get("draws", 1500))
        warmup = int(p.get("warmup", 1500))
        chains = int(p.get("chains", 4))
        seed = int(p.get("seed", 42))
        n_post = int(p.get("posterior_samples", 3000))
        backend = p.get("backend", "laplace")

        samples = diag = None
        if backend == "pymc":
            samples, diag = _fit_pymc(spec, draws, warmup, chains, seed, warnings)
        elif backend == "metropolis":
            samples, diag = _metropolis(spec, draws, warmup, chains, seed)
        if samples is None:  # default + fallback path
            samples, diag = _fit_laplace(spec, n_post, seed, warnings)
        if samples is None:  # last resort if MAP failed
            samples, diag = _metropolis(spec, draws, warmup, chains, seed)

        rhat = diag.get("max_rhat")
        if not diag.get("rhat_ok", False):
            if rhat is not None:
                warnings.append(
                    f"Convergence weak (max R-hat={rhat:.2f} >= 1.1). Increase "
                    f"draws/warmup or simplify the model before trusting estimates.")
            else:
                warnings.append(
                    "Some parameters are weakly identified (flat posterior "
                    "directions). Treat their channel estimates as prior-dominated.")

        # Posterior derived quantities
        n_s = samples.shape[0]
        sub = samples[np.linspace(0, n_s - 1, min(600, n_s)).astype(int)]
        contribs = np.zeros((len(sub), spec.M))
        baselines = np.zeros(len(sub))
        incrementals = np.zeros(len(sub))
        decays = np.zeros((len(sub), spec.M))
        for i, th in enumerate(sub):
            d = _decompose(th, spec)
            contribs[i] = d["contrib_by_channel"]
            baselines[i] = d["baseline_total"]
            incrementals[i] = d["incremental_total"]
            decays[i] = d["decay"]

        spend_totals = spec.media_raw.sum(0)
        y_total = float(spec.y.sum())
        theta_mean = sub.mean(0)  # response curves evaluated at the posterior mean

        channels = []
        for g in range(spec.M):
            c = contribs[:, g]
            spend_g = float(spend_totals[g])
            roi = c / spend_g * 1000.0 if spend_g > 0 else np.zeros_like(c)  # incr units / $1k
            contrib_mean = float(c.mean())
            # cost per incremental unit from the aggregate (not mean of per-sample
            # ratios, which explodes when a sample's contribution is ~0)
            cpa = (spend_g / contrib_mean) if contrib_mean > 1e-6 else None
            channels.append({
                "channel": groups[g],
                "total_spend": spend_g,
                "contribution_mean": contrib_mean,
                "contribution_ci90": [_pct(c, 5), _pct(c, 95)],
                "pct_of_outcome": float(contrib_mean / y_total * 100) if y_total else None,
                "roi_per_1k_mean": float(roi.mean()),
                "roi_per_1k_ci90": [_pct(roi, 5), _pct(roi, 95)],
                "cost_per_incremental_unit": cpa,
                "adstock_decay_mean": float(decays[:, g].mean()),
                "response_curve": _response_curve(theta_mean, spec, g),
            })

        # rank by marginal ROI for reallocation guidance
        ranked = sorted(channels, key=lambda x: x["roi_per_1k_mean"], reverse=True)
        out = {
            "run_id": run_id,
            "outcome_kind": manifest.get("outcome_kind"),
            "granularity": manifest.get("granularity"),
            "n_periods": spec.T,
            "y_total": y_total,
            "baseline": {
                "mean": float(baselines.mean()),
                "ci90": [_pct(baselines, 5), _pct(baselines, 95)],
                "pct_of_outcome": float(baselines.mean() / y_total * 100) if y_total else None,
            },
            "incremental": {
                "mean": float(incrementals.mean()),
                "ci90": [_pct(incrementals, 5), _pct(incrementals, 95)],
                "pct_of_outcome": float(incrementals.mean() / y_total * 100) if y_total else None,
            },
            "channels": channels,
            "reallocation_ranking": [c["channel"] for c in ranked],
            "diagnostics": diag,
        }
        out_path = results_path(run_id, "marketing_mix_model")
        write_json(out_path, out)

        top = ranked[0]["channel"] if ranked else "n/a"
        bot = ranked[-1]["channel"] if ranked else "n/a"
        conv = (f"max R-hat={rhat:.2f}" if rhat is not None
                else f"{diag.get('n_flat_directions', 0)} flat directions")
        emit({
            "results": out,
            "summary": (
                f"MMM over {spec.T} {manifest.get('granularity')}s, {spec.M} channels "
                f"({diag['sampler']}, {conv}). "
                f"Baseline {out['baseline']['pct_of_outcome']:.0f}% of outcome; "
                f"highest marginal ROI: {top}, lowest: {bot}. "
                f"{'Convergence/feasibility WARNINGS present.' if warnings else ''}"),
            "metadata": {
                "n_records": spec.T, "n_features": spec.M,
                "warnings": warnings,
                "artifacts": {"json": str(out_path)},
            },
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

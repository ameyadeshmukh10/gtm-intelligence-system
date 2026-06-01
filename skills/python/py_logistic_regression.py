"""py_logistic_regression — regularized L2 logistic regression for directional signals.

Complements Random Forest: RF ranks importance (magnitude), logistic gives
direction (+/-). Negative coefficients on high-volume programs often indicate
programs that dilute quality after controlling for other signals.

Optionally generates pairwise interaction terms from binary rollups.

Params:
  run_id:   str
  target:   str
  features: list[str]   default = manifest feature set
  include_interactions: bool  default False
  interaction_cols:    list[str]  if include_interactions, use these binary cols
  C:        float       default 0.1
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run, feature_columns, binary_columns


def main():
    try:
        p = read_params()
        df, manifest, target = load_run(p)
        feats = p.get("features") or feature_columns(manifest)
        feats = [c for c in feats if c in df.columns and c != target]

        X = df[feats].apply(pd.to_numeric, errors="coerce")
        X = X.fillna(X.median(numeric_only=True)).fillna(0)

        # Optional interaction terms
        if p.get("include_interactions"):
            inter_cols = p.get("interaction_cols") \
                or [c for c in binary_columns(manifest) if c in df.columns][:20]
            added = []
            for a, b in combinations(inter_cols, 2):
                if a in df.columns and b in df.columns:
                    name = f"int_{a}_x_{b}"
                    X[name] = (df[a] * df[b]).astype(float)
                    added.append(name)
            feats = feats + added

        scaler = StandardScaler(with_mean=False)  # keep binary 0/1 interpretable
        Xs = scaler.fit_transform(X)
        y = df[target].astype(int).values

        model = LogisticRegression(C=p.get("C", 0.1),
                                    class_weight="balanced",
                                    solver="liblinear", max_iter=2000)
        model.fit(Xs, y)
        coefs = [{"feature": f, "coef": float(c)}
                 for f, c in zip(feats, model.coef_[0])]
        pos = sorted([c for c in coefs if c["coef"] > 0],
                      key=lambda x: x["coef"], reverse=True)[:10]
        neg = sorted([c for c in coefs if c["coef"] < 0],
                      key=lambda x: x["coef"])[:10]

        out = results_path(p["run_id"], "logistic_regression")
        write_json(out, {"target": target, "all_coefs": coefs,
                         "positive": pos, "negative": neg,
                         "intercept": float(model.intercept_[0]),
                         "C": p.get("C", 0.1)})
        emit({
            "results": {"positive": pos, "negative": neg,
                        "intercept": float(model.intercept_[0])},
            "summary": (
                f"Logistic regression fitted on {len(feats)} features. "
                f"Top positive: {pos[0]['feature'] if pos else 'none'} "
                f"({pos[0]['coef']:.2f} if pos else ''). "
                f"Top negative: {neg[0]['feature'] if neg else 'none'} "
                f"({neg[0]['coef']:.2f} if neg else '').").replace(
                    " if pos else ''", "").replace(" if neg else ''", ""),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(feats),
                         "warnings": [],
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

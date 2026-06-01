"""py_random_forest — balanced RF with 5-fold stratified CV and feature importance.

Flags AUC > 0.95 as likely data leakage and surfaces the top features to
investigate (usually downstream consequences of the outcome).

Params:
  run_id:    str
  target:    str
  features:  list[str]   default = full manifest feature set
  n_estimators: int      default 300
  drop_zero_variance: bool default True
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.metrics import roc_auc_score, f1_score

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run, feature_columns


def main():
    try:
        p = read_params()
        df, manifest, target = load_run(p)
        feats = p.get("features") or feature_columns(manifest)
        feats = [c for c in feats if c in df.columns and c != target]

        X = df[feats].copy()
        # drop zero-variance
        if p.get("drop_zero_variance", True):
            var = X.var(numeric_only=True)
            keep = [c for c in feats if c not in var.index or var.get(c, 0) > 0]
            dropped = [c for c in feats if c not in keep]
            feats = keep
            X = X[feats]

        # fill any remaining NaNs and coerce to numeric
        X = X.apply(pd.to_numeric, errors="coerce")
        X = X.fillna(X.median(numeric_only=True)).fillna(0)
        y = df[target].astype(int).values

        warnings = []
        if X.shape[1] == 0:
            emit_error("No usable features after filtering")
            return
        if y.sum() < 10 or (len(y) - y.sum()) < 10:
            warnings.append(
                f"Class imbalance extreme: pos={int(y.sum())}, neg={int(len(y)-y.sum())}. "
                f"Results may be unstable.")

        rf = RandomForestClassifier(
            n_estimators=p.get("n_estimators", 300),
            class_weight="balanced", n_jobs=-1, random_state=42)
        cv = StratifiedKFold(n_splits=min(5, max(2, int(y.sum() // 2))),
                              shuffle=True, random_state=42)
        auc_scores = cross_val_score(rf, X, y, cv=cv, scoring="roc_auc", n_jobs=-1)
        f1_scores = cross_val_score(rf, X, y, cv=cv, scoring="f1", n_jobs=-1)
        auc = float(np.mean(auc_scores)); f1 = float(np.mean(f1_scores))

        rf.fit(X, y)
        importances = sorted(
            [{"feature": c, "importance": float(imp)}
             for c, imp in zip(feats, rf.feature_importances_)],
            key=lambda r: r["importance"], reverse=True)

        if auc > 0.95:
            warnings.append(
                f"AUC={auc:.3f} > 0.95 — likely data leakage. "
                f"Review top features: {[r['feature'] for r in importances[:5]]}")

        out = results_path(p["run_id"], "random_forest")
        write_json(out, {
            "target": target, "auc": auc, "f1": f1,
            "auc_folds": [float(x) for x in auc_scores],
            "f1_folds": [float(x) for x in f1_scores],
            "importances": importances,
            "n_features": len(feats),
        })

        emit({
            "results": {
                "auc": auc, "f1": f1,
                "top_features": importances[:20],
                "n_features": len(feats),
                "n_records": int(len(df)),
            },
            "summary": (
                f"RF AUC={auc:.3f}, F1={f1:.3f} across {len(feats)} features, "
                f"{len(df)} records. Top predictor: "
                f"{importances[0]['feature'] if importances else 'none'}."),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(feats),
                         "warnings": warnings,
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

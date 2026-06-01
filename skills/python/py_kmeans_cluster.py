"""py_kmeans_cluster — KMeans archetypes with silhouette scoring k=3,4,5.

Auto-resolves features from the manifest. Standardizes before clustering.
Selects the best k by silhouette, returns cluster profiles with deal rates
and top defining traits (z-scored vs overall mean).

Params:
  run_id:   str
  target:   str
  features: list[str] (default = manifest feature set)
  k_values: list[int] (default [3,4,5])
  sample:   int        default 10000 (perf cap for silhouette)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run, feature_columns


def main():
    try:
        p = read_params()
        df, manifest, target = load_run(p)
        feats = p.get("features") or feature_columns(manifest)
        feats = [c for c in feats if c in df.columns and c != target]
        if not feats:
            emit_error("No features available for clustering")
            return

        X = df[feats].apply(pd.to_numeric, errors="coerce")
        X = X.fillna(X.median(numeric_only=True)).fillna(0)
        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)

        k_values = p.get("k_values", [3, 4, 5])
        sample_cap = p.get("sample", 10000)
        idx_for_sil = (np.random.default_rng(42)
                       .choice(len(Xs), size=min(sample_cap, len(Xs)),
                               replace=False))

        silhouettes = {}
        fits = {}
        for k in k_values:
            km = KMeans(n_clusters=k, n_init=10, random_state=42)
            labels = km.fit_predict(Xs)
            try:
                s = silhouette_score(Xs[idx_for_sil], labels[idx_for_sil])
            except Exception:
                s = float("nan")
            silhouettes[k] = float(s)
            fits[k] = (km, labels)

        best_k = max(silhouettes, key=lambda k: silhouettes[k]
                     if not np.isnan(silhouettes[k]) else -1)
        km, labels = fits[best_k]

        overall_mean = X.mean()
        overall_rate = float(df[target].mean())

        profiles = []
        for c in range(best_k):
            mask = labels == c
            cluster_rate = float(df.loc[mask, target].mean()) if mask.any() else None
            cluster_mean = X.loc[mask].mean()
            diffs = (cluster_mean - overall_mean) / (X.std().replace(0, 1))
            # Top 3 defining traits by absolute z-score
            top = diffs.abs().sort_values(ascending=False).head(5)
            traits = [{"feature": name,
                       "cluster_mean": float(cluster_mean[name]),
                       "overall_mean": float(overall_mean[name]),
                       "z_gap": float(diffs[name])}
                      for name in top.index]
            profiles.append({
                "cluster": int(c),
                "n": int(mask.sum()),
                "rate": cluster_rate,
                "lift_vs_overall": (cluster_rate - overall_rate
                                    if cluster_rate is not None else None),
                "top_defining_traits": traits,
            })

        profiles.sort(key=lambda x: (x["rate"] or 0), reverse=True)

        out = results_path(p["run_id"], "kmeans_cluster")
        write_json(out, {
            "target": target, "k_values": k_values,
            "silhouettes": silhouettes, "best_k": best_k,
            "overall_rate": overall_rate,
            "profiles": profiles,
        })

        # Gap assessment
        best_rate = max(profiles, key=lambda x: x["rate"] or 0)["rate"] or 0
        worst_rate = min(profiles, key=lambda x: x["rate"] or 1)["rate"] or 1
        gap_ratio = (best_rate / worst_rate) if worst_rate > 0 else None

        emit({
            "results": {
                "best_k": best_k,
                "silhouettes": silhouettes,
                "overall_rate": overall_rate,
                "profiles": profiles,
                "best_vs_worst_ratio": gap_ratio,
            },
            "summary": (
                f"KMeans best k={best_k} (silhouette={silhouettes[best_k]:.3f}). "
                f"Best cluster rate {best_rate:.2%} vs worst {worst_rate:.2%}"
                + (f" ({gap_ratio:.1f}x gap)" if gap_ratio else "") + "."),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(feats),
                         "warnings": [],
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

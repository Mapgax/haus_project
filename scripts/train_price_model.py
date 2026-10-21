#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hausfinder.modeling import export_training_csv, load_training_rows


def _label_rows(rows: list[dict[str, float | str | int]]) -> tuple[list[dict[str, float | str | int]], list[int]]:
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        key = (str(row["category"]), str(row["location_bucket"]))
        grouped.setdefault(key, []).append(float(row["ppsqm"]))

    medians: dict[tuple[str, str], float] = {}
    for key, vals in grouped.items():
        s = sorted(vals)
        n = len(s)
        medians[key] = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0

    x: list[dict[str, float | str | int]] = []
    y: list[int] = []
    for row in rows:
        key = (str(row["category"]), str(row["location_bucket"]))
        median = medians[key]
        ratio = float(row["ppsqm"]) / median if median > 0 else 1.0
        if ratio <= 0.92:
            x.append(row)
            y.append(0)  # guenstiger als baseline
        elif ratio >= 1.08:
            x.append(row)
            y.append(1)  # teurer als baseline
    return x, y


def train(db_path: str, model_path: str, metrics_path: str) -> dict[str, float | int]:
    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, f1_score
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
        import joblib
    except ImportError as exc:
        raise SystemExit(
            "Missing ML dependencies. Install with: pip install -e .[ml]"
        ) from exc

    rows = load_training_rows(db_path)
    x, y = _label_rows(rows)
    if len(x) < 30:
        raise SystemExit("Not enough labeled samples yet (need >= 30). Keep collecting data.")

    dict_x = [
        {
            "category": str(r["category"]),
            "source_name": str(r["source_name"]),
            "location_bucket": str(r["location_bucket"]),
            "area_sqm": float(r["area_sqm"]),
            "rooms": float(r["rooms"]),
            "score": float(r["score"]),
            "condition_score": float(r["condition_score"]),
        }
        for r in x
    ]
    x_train, x_test, y_train, y_test = train_test_split(
        dict_x, y, test_size=0.25, random_state=42, stratify=y
    )

    pipeline = Pipeline(
        steps=[
            ("vec", DictVectorizer(sparse=False)),
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=500)),
        ]
    )
    pipeline.fit(x_train, y_train)
    preds = pipeline.predict(x_test)

    metrics = {
        "samples_total": len(dict_x),
        "samples_test": len(x_test),
        "accuracy": float(accuracy_score(y_test, preds)),
        "f1": float(f1_score(y_test, preds)),
    }

    model_out = Path(model_path)
    model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, model_out)

    metrics_out = Path(metrics_path)
    metrics_out.parent.mkdir(parents=True, exist_ok=True)
    metrics_out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Train expensive-vs-cheap baseline model.")
    parser.add_argument("--db-path", default="data/seen.sqlite3")
    parser.add_argument("--export-csv", default="data/model/training_rows.csv")
    parser.add_argument("--model-path", default="data/model/price_classifier.joblib")
    parser.add_argument("--metrics-path", default="data/model/metrics.json")
    args = parser.parse_args()

    n = export_training_csv(args.db_path, args.export_csv)
    metrics = train(args.db_path, args.model_path, args.metrics_path)
    print(f"Exported rows: {n}")
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


"""Blocked validation of existing monthly calibration candidates."""

from pathlib import Path

import pandas as pd


def main():
    source = Path("outputs/baseline_v2_daynight/monthly_inputs.csv")
    destination = Path("outputs/stress_test_v1")

    monthly = pd.read_csv(
        source, dtype={"month": str, "modis_versions": str}
    )

    required = {
        "month", "year", "viirs", "modis_pooled",
        "modis_pooled_D", "modis_pooled_N", "viirs_D", "viirs_N",
    }
    if not required.issubset(monthly.columns):
        raise ValueError(f"Missing columns: {sorted(required - set(monthly))}")

    expected = set(
        pd.period_range("2019-01", "2024-12", freq="M").astype(str)
    )
    if len(monthly) != 72 or set(monthly["month"]) != expected:
        raise ValueError("Expected 72 unique development months")

    numeric = sorted(required - {"month"})
    if monthly[numeric].isna().any().any():
        raise ValueError("Missing numeric input")

    folds = {
        "holdout_2019_2020": (
            monthly["year"].between(2021, 2024),
            monthly["year"].between(2019, 2020),
        ),
        "forward_2023_2024": (
            monthly["year"].between(2019, 2022),
            monthly["year"].between(2023, 2024),
        ),
    }

    outputs = []

    for fold, (train_mask, test_mask) in folds.items():
        training = monthly.loc[train_mask]
        testing = monthly.loc[test_mask].copy()

        factors = {}
        for key in ("pooled", "D", "N"):
            modis_column = (
                "modis_pooled" if key == "pooled"
                else f"modis_pooled_{key}"
            )
            viirs_column = "viirs" if key == "pooled" else f"viirs_{key}"

            denominator = training[modis_column].sum()
            if denominator <= 0:
                raise ValueError(f"{fold}: invalid training denominator")
            factors[key] = training[viirs_column].sum() / denominator

        estimates = {
            "single_multiplier": (
                testing["modis_pooled"] * factors["pooled"]
            ),
            "daynight_multipliers": (
                testing["modis_pooled_D"] * factors["D"]
                + testing["modis_pooled_N"] * factors["N"]
            ),
        }

        for method, estimate in estimates.items():
            result = testing[["month", "year", "viirs"]].copy()
            result["fold"] = fold
            result["method"] = method
            result["estimate"] = estimate
            result["error"] = estimate - result["viirs"]
            result["absolute_error"] = result["error"].abs()
            result["multiplier_pooled"] = (
                factors["pooled"] if method == "single_multiplier"
                else float("nan")
            )
            result["multiplier_day"] = (
                factors["D"] if method == "daynight_multipliers"
                else float("nan")
            )
            result["multiplier_night"] = (
                factors["N"] if method == "daynight_multipliers"
                else float("nan")
            )
            outputs.append(result)

    predictions = pd.concat(outputs, ignore_index=True)
    scores = (
        predictions.groupby(["fold", "method"])
        .agg(
            months=("month", "size"),
            mae=("absolute_error", "mean"),
            absolute_error_sum=("absolute_error", "sum"),
            signed_error_sum=("error", "sum"),
            reference_total=("viirs", "sum"),
        )
        .reset_index()
    )

    denominator = scores["reference_total"].replace(0, float("nan"))
    scores["wape_percent"] = (
        100 * scores["absolute_error_sum"] / denominator
    )
    scores["bias_percent"] = (
        100 * scores["signed_error_sum"] / denominator
    )

    destination.mkdir(parents=True, exist_ok=False)
    predictions.to_csv(destination / "predictions.csv", index=False)
    scores.to_csv(destination / "scores.csv", index=False)

    print(scores.to_string(index=False))
    print("\nExploratory sensor agreement; coverage remains unknown.")


if __name__ == "__main__":
    main()
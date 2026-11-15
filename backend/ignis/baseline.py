"""Exploratory leave-one-year-out monthly count calibration."""

from pathlib import Path

import pandas as pd


def main():
    source = Path("outputs/historical_preparation_v1")
    destination = Path("outputs/baseline_v2_daynight")

    modis = pd.read_csv(
        source / "modis_vegetation_monthly_development.csv",
        dtype={"version": str, "month": str},
    )
    viirs = pd.read_csv(
        source / "viirs_vegetation_monthly_development.csv",
        dtype={"version": str, "month": str},
    )

    expected = pd.period_range("2019-01", "2024-12", freq="M").astype(str)

    for frame in (modis, viirs):
        if not frame["dataset_role"].eq("development").all():
            raise ValueError("Only development data is allowed")
        if set(frame["month"]) != set(expected):
            raise ValueError("Expected exactly January 2019–December 2024")

    # These are additive record counts, not summed active-cell-day counts.
    monthly = pd.DataFrame(index=pd.Index(expected, name="month"))
    monthly["viirs"] = viirs.groupby("month")["records"].sum()
    monthly["modis_pooled"] = modis.groupby("month")["records"].sum()
    monthly["modis_aqua"] = (
        modis.loc[modis["satellite"].eq("Aqua")]
        .groupby("month")["records"].sum()
    )
    monthly["modis_versions"] = (
        modis.groupby("month")["version"]
        .agg(lambda values: "|".join(sorted(set(values))))
    )

    if monthly.isna().any().any():
        raise ValueError("Missing monthly input; investigate before proceeding")


    for daynight in ("D", "N"):
        monthly[f"modis_pooled_{daynight}"] = (
            modis.loc[modis["daynight"].eq(daynight)]
            .groupby("month")["records"]
            .sum()
            .reindex(monthly.index, fill_value=0)
        )
        monthly[f"viirs_{daynight}"] = (
            viirs.loc[viirs["daynight"].eq(daynight)]
            .groupby("month")["records"]
            .sum()
            .reindex(monthly.index, fill_value=0)
        )

    # Missing strata mean no retained records, not confirmed coverage.
    for prefix, total_column in (
        ("modis_pooled", "modis_pooled"),
        ("viirs", "viirs"),
    ):
        reconstructed = monthly[f"{prefix}_D"] + monthly[f"{prefix}_N"]
        if not reconstructed.eq(monthly[total_column]).all():
            raise ValueError(f"{prefix}: day/night totals do not reconcile")
        
    monthly = monthly.reset_index()
    monthly["year"] = monthly["month"].str[:4].astype(int)
    predictions = []

    for platform in ("modis_pooled", "modis_aqua"):
        for year in range(2019, 2025):
            training = monthly.loc[monthly["year"].ne(year)]
            testing = monthly.loc[monthly["year"].eq(year)].copy()

            denominator = training[platform].sum()
            if denominator <= 0:
                raise ValueError("Training MODIS total must be positive")
            factor = training["viirs"].sum() / denominator

            for method, multiplier in (
                ("raw_unadjusted", 1.0),
                ("training_total_ratio", factor),
            ):
                result = testing[
                    ["month", "year", "modis_versions", "viirs"]
                ].copy()
                result["platform"] = platform
                result["method"] = method
                result["multiplier"] = multiplier
                result["estimate"] = testing[platform] * multiplier
                result["error"] = result["estimate"] - result["viirs"]
                result["absolute_error"] = result["error"].abs()
                predictions.append(result)

    # Candidate 2: separate daytime/nighttime adjustments for pooled MODIS.
    for year in range(2019, 2025):
        training = monthly.loc[monthly["year"].ne(year)]
        testing = monthly.loc[monthly["year"].eq(year)].copy()

        factors = {}
        for daynight in ("D", "N"):
            denominator = training[f"modis_pooled_{daynight}"].sum()
            if denominator <= 0:
                raise ValueError(
                    f"{year}: no training MODIS records for {daynight}"
                )
            factors[daynight] = (
                training[f"viirs_{daynight}"].sum() / denominator
            )

        result = testing[
            ["month", "year", "modis_versions", "viirs"]
        ].copy()
        result["platform"] = "modis_pooled"
        result["method"] = "daynight_training_total_ratio"

        # There is no single multiplier for this method.
        result["multiplier"] = float("nan")
        result["multiplier_day"] = factors["D"]
        result["multiplier_night"] = factors["N"]

        result["estimate"] = (
            testing["modis_pooled_D"] * factors["D"]
            + testing["modis_pooled_N"] * factors["N"]
        )
        result["error"] = result["estimate"] - result["viirs"]
        result["absolute_error"] = result["error"].abs()
        predictions.append(result)
        
    predictions = pd.concat(predictions, ignore_index=True)
    scores = (
        predictions.groupby(["platform", "method"])
        .agg(
            months=("month", "size"),
            mae=("absolute_error", "mean"),
            signed_error_sum=("error", "sum"),
            reference_total=("viirs", "sum"),
        )
        .reset_index()
    )
    scores["aggregate_bias_percent"] = (
        100 * scores["signed_error_sum"] / scores["reference_total"]
    )
    
    annual_scores = (
        predictions.groupby(["platform", "method", "year"])
        .agg(
            months=("month", "size"),
            mae=("absolute_error", "mean"),
            absolute_error_sum=("absolute_error", "sum"),
            signed_error_sum=("error", "sum"),
            reference_total=("viirs", "sum"),
        )
        .reset_index()
    )

    denominator = annual_scores["reference_total"].replace(
        0, float("nan")
    )
    annual_scores["wape_percent"] = (
        100 * annual_scores["absolute_error_sum"] / denominator
    )
    annual_scores["aggregate_bias_percent"] = (
        100 * annual_scores["signed_error_sum"] / denominator
    )

    destination.mkdir(parents=True, exist_ok=False)
    monthly.to_csv(destination / "monthly_inputs.csv", index=False)
    predictions.to_csv(destination / "held_out_predictions.csv", index=False)
    scores.to_csv(destination / "scores.csv", index=False)
    annual_scores.to_csv(destination / "annual_scores.csv", index=False)

    print(scores.to_string(index=False))
    print(f"\nSaved to {destination.resolve()}")
    print("Exploratory sensor agreement only; coverage remains unknown.")


if __name__ == "__main__":
    main()
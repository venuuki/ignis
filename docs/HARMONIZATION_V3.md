# Harmonization v3: common scale, explicit evidence

The Space Apps challenge asks for a comparable historical burning-activity calendar across MODIS and VIIRS. It leaves the numerical algorithm open. This implementation uses reference-scale statistical transfer; it does not deduplicate records into verified individual fires, sum overlapping sensors, or infer burned area.

## Pipeline

1. The imported Australian Parquets retain native observations, acquisition times, satellite, confidence, vegetation/source classification and quality flags.
2. Quality-filtered detections are aggregated to common 0.1-degree cell-month support, retaining MODIS satellite/day-night channels and fire radiative power. Surrounding activity, MODIS climatology and season/location features are available to candidate models.
3. For evaluation, climatology features and cross-platform scaling are learned from training years only: 2013, 2016, 2018 and 2023. The Terra-only model uses Terra-only FRP rather than Terra and Aqua FRP.
4. Calibration years 2017 and 2022 select a candidate and fit residual uncertainty, including area-level error correlation inflation. These are not the reported final-test years.
5. Final-test years 2019, 2020 and 2021 assess the frozen selected model. `validation_cells.parquet` preserves those predictions before any production refit. AOI validation aggregates this separate file, not the refitted production predictions. This is retrospective held-out sensor-reference testing, not an independent field survey or prospective deployment trial.
6. Production models are refitted on the available overlap years. The historical activity series preserves calibrated MODIS through its supplied support period, then continues with the VIIRS reference. VIIRS remains the training/comparison reference during overlap. `modis_harmonized` is the default activity output during overlap as well as earlier years.
7. `native_snpp_monthly.parquet` preserves actual native S-NPP counts independently of production sparsification and reference-platform selection. It is a diagnostic observation series, not the reference gap-filling product.

A single multiplier is not assumed in advance. Global day/night ratios, hierarchical empirical-Bayes transfer and LightGBM compete on the calibration years. This build selected `global_ratio` for the main record and `gbm` for the Terra-only period. All candidate test results, including failures, remain in the report.

## Measurements and source distinctions

- Harmonized: calibrated MODIS through its supplied support period, followed by VIIRS reference on the same scale. Production overlap comparisons can be in-sample.
- MODIS observations: native quality-filtered Terra/Aqua counts, not calibrated values.
- VIIRS reference: measured S-NPP where available and scaled NOAA-platform counts for reference gaps. It is not labelled raw VIIRS.
- Internal S-NPP diagnostic, not a selectable UI series: native S-NPP counts from the original quality-filtered observations, without platform gap filling. A month without supplied S-NPP detections is unavailable; a zero within a supplied month means no recorded detections for that area, not verified no fire.

The shared spatial support and day/night transfer reduce sensor-related differences; they do not recover observation exposure. Platform-selection rules based on detection availability are heuristics. NOAA gap filling, spatial/temporal extrapolation, orbit drift and missed observations retain uncertainty. The current fallback uses the NOAA-20-to-S-NPP calibration for NOAA-platform gap filling and does not independently validate every NOAA-21 gap scenario.

## Uncertainty and anomalies

Residual calibration intervals apply to model estimates only. Fractional-area model variance is weighted by squared fractions, with a spatial correlation inflation factor; within-cell spatial allocation remains approximate. Terra-only periods use Terra-model interval calibration. Reference observations and scaled references have no claimed zero-width confidence interval: observational/omission and platform-scaling uncertainty are not fully quantified.

On the national final test, selected-model yearly absolute error is 5.58%, monthly MAPE 11.65%, monthly WAPE approximately 10.2%, and signed bias -4.37%. The naive MODIS yearly error is 79.33%. Country-scale nominal 80% residual interval coverage is 72.22%; region-scale coverage is 85.99%. These metrics describe different aggregation scales and are not interchangeable with fire-detection accuracy.

Calendar baselines display their effective years/sample sizes. Raw S-NPP cannot supply pre-2012 baseline observations. Incomplete months retain their observed count and temporal-availability flag, but no linear full-month extrapolation or full-month anomaly is assigned. Missing values remain distinct from zero. Trend/ranking calculations require complete available years; unavailable raw-sensor years are not treated as complete zero years.

## Daily exploration and forecasting

Daily queries use the actual native observations and exact selection geometry. The monthly transfer model is not asserted to be a daily model. Point tables, per-platform daily counts and the selected-day map are consistent with the same filtered archive query; boundaries of monthly grid aggregation remain area-weighted approximations.

Forecasts were rebuilt from the v3.1 calibrated MODIS history, using the existing climate outputs. Their report records the harmonization version and build time. Incompatible forecasts are withheld. Country skill is used only for an essentially complete national AOI; other areas use regional validation and fall back to climatology when that skill is nonpositive. Rolling-origin forecast skill is conditional on retrospectively calibrated history, not a fully prospective end-to-end validation.

## Reproducibility and source interpretation

The raw input inventory/checksums are in `data/australia_parquet_import.json`. Model protocol, metrics, features and source rules are in `data/processed/harmonization_report.json`; frozen test predictions, source metadata and native diagnostics are separate Parquets. Previous outputs are backed up. The retired experiment is not exposed by the application.

NASA product interpretation: [MODIS fields](https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_MODIS_Firehotspots.html), [VIIRS fields](https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_VIIRS_Firehotspots.html), and [FIRMS interpretation notes](https://firms2.modaps.eosdis.nasa.gov/alerts/notes.php). These explain observation/product limitations; they do not endorse the project's chosen statistical model.

## Final prototype safeguards

The active source policy is `modis_then_viirs_v1`; supplied date bounds are not proof of satellite retirement or clear-sky exposure. Approximate residual ranges are not guaranteed 80% intervals for arbitrary selections. Zero-width ranges are withheld and anomaly labels require at least five baseline values. Unknown VIIRS confidence now fails historical CSV QC. An unskillful forecast with fewer than five complete baseline years is withheld.

Regional experiments and training-only cross-validation are saved under docs. The geographic candidate worsened southern test samples and was not promoted. Monthly day/night ratios pass the training-year geographic safeguards, but remain offline pending further evaluation and arbitrary-area uncertainty validation. Production parameters and Parquets remain unchanged. The automatic history updater remains removed.

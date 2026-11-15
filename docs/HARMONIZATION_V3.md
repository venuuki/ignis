# Harmonization v3: common scale, explicit evidence

The Space Apps challenge asks for a comparable historical burning-activity calendar across MODIS and VIIRS. It leaves the numerical algorithm open. This implementation uses reference-scale statistical transfer; it does not deduplicate records into verified individual fires, sum overlapping sensors, or infer burned area.

## Pipeline

1. The imported Australian Parquets retain native observations, acquisition times, satellite, confidence, vegetation/source classification and quality flags.
2. Quality-filtered detections are aggregated to common 0.1-degree cell-month support, retaining MODIS satellite/day-night channels and fire radiative power. Surrounding activity, MODIS climatology and season/location features are available to candidate models.
3. For evaluation, climatology features and cross-platform scaling are learned from training years only: 2013, 2016, 2018 and 2023. The Terra-only model uses Terra-only FRP rather than combined Terra/Aqua FRP.
4. Calibration years 2017 and 2022 select a candidate and fit residual uncertainty, including area-level error correlation inflation. These are not the reported final-test years.
5. Final-test years 2019, 2020 and 2021 assess the frozen selected model. `validation_cells.parquet` preserves those predictions before any production refit. AOI validation aggregates this separate file, not the refitted production predictions. This is retrospective held-out sensor-reference testing, not an independent field survey or prospective deployment trial.
6. Production models are refitted on the available overlap years. The continuous series uses the VIIRS reference where available and calibrated MODIS otherwise. `modis_harmonized` remains a separate counterfactual MODIS-derived estimate during overlap, so the model can be inspected rather than hidden by reference substitution.
7. `native_snpp_monthly.parquet` preserves actual native S-NPP counts independently of production sparsification and reference-platform selection. It is a diagnostic observation series, not the reference gap-filling product.

A single multiplier is not assumed in advance. Global day/night ratios, hierarchical empirical-Bayes transfer and LightGBM compete on the calibration years. This build selected `global_ratio` for the main record and `gbm` for the Terra-only period. All candidate test results, including failures, remain in the report.

## Measurements and source distinctions

- Combined record: S-NPP-equivalent monthly activity from calibrated MODIS and the VIIRS reference.
- Calibrated MODIS: production transfer predictions throughout the available MODIS record. Production overlap comparisons can be in-sample.
- MODIS observations: native quality-filtered Terra/Aqua counts, not calibrated values.
- VIIRS reference: measured S-NPP where available and scaled NOAA-platform counts for reference gaps. It is not labelled raw VIIRS.
- S-NPP only: native S-NPP counts from the original quality-filtered observations, without platform gap filling. A month without supplied S-NPP detections is unavailable; a zero within a supplied month means no recorded detections for that area, not verified no fire.

The shared spatial support and day/night transfer reduce sensor-related differences; they do not recover observation exposure. Platform-selection rules based on detection availability are heuristics. NOAA gap filling, spatial/temporal extrapolation, orbit drift and missed observations retain uncertainty. The current fallback uses the NOAA-20-to-S-NPP calibration for NOAA-platform gap filling and does not independently validate every NOAA-21 gap scenario.

## Uncertainty and anomalies

Residual calibration intervals apply to model estimates only. Fractional-area model variance is weighted by squared fractions, with a spatial correlation inflation factor; within-cell spatial allocation remains approximate. Terra-only periods use Terra-model interval calibration. Reference observations and scaled references have no claimed zero-width confidence interval: observational/omission and platform-scaling uncertainty are not fully quantified.

On the national final test, selected-model yearly absolute error is 5.58%, monthly MAPE 11.65%, monthly WAPE approximately 10.2%, and signed bias -4.37%. The naive MODIS yearly error is 79.33%. Country-scale nominal 80% residual interval coverage is 72.22%; region-scale coverage is 85.99%. These metrics describe different aggregation scales and are not interchangeable with fire-detection accuracy.

Calendar baselines display their effective years/sample sizes. Raw S-NPP cannot supply pre-2012 baseline observations. Incomplete months retain their observed count and temporal-availability flag, but no linear full-month extrapolation or full-month anomaly is assigned. Missing values remain distinct from zero. Trend/ranking calculations require complete available years; unavailable raw-sensor years are not treated as complete zero years.

## Daily exploration and forecasting

Daily queries use the actual native observations and exact selection geometry. The monthly transfer model is not asserted to be a daily model. Point tables, per-platform daily counts and the selected-day map are consistent with the same filtered archive query; boundaries of monthly grid aggregation remain area-weighted approximations.

Forecasts were rebuilt from the v3 combined record, using the existing climate outputs. Their report records the harmonization version and build time. Incompatible forecasts are withheld. Country skill is used only for an essentially complete national AOI; other areas use regional validation and fall back to climatology when that skill is nonpositive. Rolling-origin forecast skill is conditional on retrospectively calibrated history, not a fully prospective end-to-end validation.

## Reproducibility and source interpretation

The raw input inventory/checksums are in `data/australia_parquet_import.json`. Model protocol, metrics, features and source rules are in `data/processed/harmonization_report.json`; frozen test predictions, source metadata and native diagnostics are separate Parquets. Previous outputs are backed up. The original southeastern experiment remains isolated from the national model.

NASA product interpretation: [MODIS fields](https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_MODIS_Firehotspots.html), [VIIRS fields](https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_VIIRS_Firehotspots.html), and [FIRMS interpretation notes](https://firms2.modaps.eosdis.nasa.gov/alerts/notes.php). These explain observation/product limitations; they do not endorse the project's chosen statistical model.

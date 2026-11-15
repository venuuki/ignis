# Embercal ? Australia burning activity calendar

Embercal is an Australia-focused NASA Space Apps research prototype. It puts MODIS history and VIIRS continuity on a common reference scale, with explicit data sources and independent model testing. The existing Revamp visual design, OpenStreetMap, Natural Earth fallback and original research archives are retained.

## Open the app

The supplied Australian observations, rebuilt v3 calendar and experimental forecasts are already installed in this checkout. In PowerShell from this directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\Start.ps1
```

Open http://127.0.0.1:8010. Australia analysis is the main workspace. Select the national record, an Australian preset, coordinates, a point/radius, or a drawn polygon/circle, and select **Analyse this area**.

The five measures are the combined historical record, calibrated MODIS estimates, native MODIS observations, the gap-filled VIIRS reference, and native S-NPP observations. A modelled month, observed month and scaled-reference month are identified separately. The calibrated-MODIS view exposes the actual model outputs throughout the overlap; the combined record deliberately uses the reference when available rather than adding overlapping sensors.

## What changed in v3

Training (2013, 2016, 2018, 2023), model selection/uncertainty calibration (2017, 2022), and final testing (2019?2021) are disjoint. Learned evaluation climatology and platform ratios exclude calibration/test years. Final-test predictions are frozen in their own file before production refitting. Terra-only models use Terra-only fire radiative power.

The calibration years selected a global day/night-ratio transfer for the main record and LightGBM for the Terra-only period. The main model's national yearly absolute error against the reference is 5.58%, compared with 79.33% for unadjusted MODIS on the same test. These are sensor-reference errors, not ground-truth fire accuracy. National test coverage of the nominal 80% residual range is 72.22%; the app reports it rather than retuning on the test set.

## Explore

- **Calendar / Anomalies:** source-aware monthly history, effective baseline sample sizes, and month details. Incomplete months have observed counts but no extrapolated full-month anomaly.
- **Daily observations:** choose a day, inspect that month's native counts by satellite, and show actual selected-day observations on the map. The monthly model is not applied as an invented daily calibration.
- **Harmonization:** compare native MODIS, calibrated MODIS and the VIIRS reference; separately inspect frozen final-test predictions and errors for the actual selected area.
- **Seasons / trends:** baseline seasonality, complete-year totals, rankings and Mann?Kendall/Sen summaries.
- **Forecast / Climate:** rebuilt experimental outlooks, actual measured skill, historical-baseline fallback for subnational areas without positive regional skill, and dated climate context.
- **Methods / Model validation:** current generated protocol, all candidate test results, uncertainty performance and remaining limitations.
- **Original study archive / experiment:** the original southeastern 2019?2024 study and its failed day/night stress test remain separate. The original study's 2025 holdout policy is unchanged.

CSV and PNG exports use the actual area and metric. Calendar exports include provenance, model version, baseline and observation-coverage caveats. Daily and final-test CSVs preserve sensor/measurement context.

## Data and rebuilding

Input: 49 user-supplied Australia Parquets in `data/processed/hotspots`, 35,564,819 records through 14 November 2026. Their original import checksums and provenance are in `data/australia_parquet_import.json`. Do not repeat ingestion to use these imported files.

To regenerate locally, run harmonization and then forecast; existing climate outputs can be reused:

```powershell
powershell -ExecutionPolicy Bypass -File .\Build-Australia.ps1 -Step harmonize
powershell -ExecutionPolicy Bypass -File .\Build-Australia.ps1 -Step forecast
```

Use `-Step drivers` only when climate inputs need updating. `-Step ingest` contacts NASA and refreshes recent data; the first full archive download can be substantial. `python -m pipeline.harmonize` and `pipeline.run_all` both use the v3 implementation. Restart the API after rebuilding because data are cached. Forecast outputs must match the current harmonization version and build timestamp; stale outputs are withheld.

Previous model results are preserved under `data/model_backups`; input backups remain under `data/import_backups`. Raw observations and original `data/demo` CSVs were not rewritten by model fitting.

## Installation and verification

For a new environment run `Setup.ps1` (Python 3.10+, Node 22+). Run `Verify.ps1` for backend tests, frontend tests and TypeScript/Vite build. Frontend development uses `Start-Frontend.ps1` on port 5173, proxied to the API on 8010.

FIRMS credentials belong only in the backend process environment or `backend/.env`, never in frontend variables. No key is needed to view the imported local data. OSM needs internet for street tiles; Natural Earth is the bundled fallback.

Hotspots are detections, not individual fires or burned area. A hotspot-only archive cannot establish cloud-free viewing coverage or reconstruct all missed fires. The application displays these limits and is an experimental research tool. See `docs/HARMONIZATION_V3.md` and `VERIFICATION.md`.


## Focused application navigation

The website now contains Australia analysis and Observations. The original study calendar, day/night experiment and recent area explorer have been removed, including their dedicated routes and study-summary CSV files. Methods and model-validation presentations are documented in `docs/EmberCal_Calibration_and_Data_Methods.docx` instead of displayed in the website. Current calibration, independent-test artifacts, forecasts, climate inputs, AOI tools and exports are retained.

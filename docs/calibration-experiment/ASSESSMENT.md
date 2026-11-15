# Experiment assessment

Decision: retain the current production model. The candidate is an offline research artifact, not a deployed replacement.

Nine predefined candidates were compared on 2017. The chosen model uses 10-degree geographic tiles and calendar quarters with a 500-MODIS-count shrinkage prior, separately for day and night. Model tables were frozen before 2024-2025 evaluation; intervals were fit on 2022 only.

| Sample | National-ratio WAPE | Candidate WAPE | Change (percentage points) |
|---|---:|---:|---:|
| Australia | 19.46% | 12.00% | -7.45 |
| Northern Australia sample | 16.93% | 9.98% | -6.96 |
| Southeastern Australia sample | 15.38% | 15.77% | +0.39 |
| Southwestern Australia sample | 21.83% | 24.11% | +2.28 |

National and northern errors improved; southeastern and southwestern errors increased. Southwest signed bias improved from -9.69% to +2.00%, but absolute monthly error increased. Correcting average bias is therefore not enough. Simulated handoff error improved nationally and in the north but worsened in the two southern samples.

The new residual intervals cover 83.3-95.8% of evaluation months, depending on area. Greater coverage does not mean an 80% interval is correctly calibrated. National intervals became wider; region-specific widths narrowed. Only 12 calibration months and 24 evaluation months per sample are available, and temporal dependence remains. The interval scheme currently covers these four fixed areas only.

2024-2025 observations were excluded from all fitting in this experiment, but had previously been used by the old production refit. These are retrospective out-of-fit results, not never-used project data. The 2019-2021 diagnostics remain separate and were not rerun to select this candidate.

Next development should use training-only cross-validation and stronger region-level guardrails rather than choosing a new correction from these evaluation outcomes. The now-inspected 2024-2025 period must not be repeatedly treated as a fresh final test. Truly new prospective evaluation requires additional later observations; none were downloaded here.

No production Parquet, model, forecast, application interface or data ingestion setting was replaced. No new dependencies, APIs or external datasets were added. New files are the experiment code, synthetic unit tests, safe JSON parameter tables, an evaluation CSV and offline reports/charts.

Verification: 76 backend tests passed.

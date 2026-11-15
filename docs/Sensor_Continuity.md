# Sensor continuity implementation

Implemented 15 November 2026 in IGNIS-Revamp. Policy: modis_then_viirs_v1.

## Monthly source selection

- Preserve calibrated MODIS history, including all overlap months.
- After the final supplied MODIS month, use the existing S-NPP-equivalent VIIRS reference.
- S-NPP is observed reference; NOAA continuation remains a scaled reference using existing calibration.
- Never add or average overlapping sensor counts. A local zero never triggers switching.
- Without either supported source, show unavailable rather than a fabricated zero.
- Preserve source method and sensor in calendar output and CSV. Continuation does not inherit MODIS residual intervals; its measurement/scaling uncertainty remains unquantified.

## Source support and limits

Source bounds come from all native MODIS records in the supplied Australian Parquets, including records rejected by quality filtering. They are archive date bounds, not proof of actual satellite retirement, complete daily ingestion, clear skies, or observation opportunities. Interior missing periods and partial-month platform failures are not resolved by this policy. The final MODIS month remains MODIS-derived; switching begins in the following month. Recalculation at startup allows revised archive bounds to be recognised.

No automatic history updater, download, API key, external dataset or dependency was added. Future calendar months still require future observations to be downloaded and processed through the existing pipeline. This implementation cannot manufacture 2030 observations from 2026 data.

## Existing data verification

The supplied MODIS archive ends on 2026-11-14. All 313 current calendar months remain within its support. Comparing national totals before/after the new policy gives a maximum absolute monthly difference of 0.0. Existing calibration models, processed Parquets and frozen independent validation predictions were not overwritten or retrained.

Future harmonization builds persist the same continuation policy. Forecasts trained under an incompatible source policy are withheld once continuation months appear until rebuilt. API metadata reports the policy independently from the archived calibration method version.

## Verification

Backend regression suite: 68 tests passed. Tests include synthetic MODIS retirement with S-NPP and scaled NOAA continuation, overlap preservation with zero local MODIS counts, no-source months, absence of false zero-width intervals, persisted pipeline selection and native records rejected by QC.

These tests establish software/source-selection behaviour, not new post-retirement scientific accuracy. Independent regional transfer validation and stronger uncertainty calibration remain subsequent work.

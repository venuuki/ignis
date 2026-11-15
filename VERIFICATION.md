# EmberCal verification

Verified on 15 November 2026 in D:/Desktop/IGNIS-Revamp.

- Backend: 82 tests passed. One upstream Starlette/AnyIO deprecation warning remains.
- Frontend: 9 tests passed; TypeScript and Vite production build passed.
- Continuity: current national monthly values unchanged; supplied MODIS archive ends 2026-11-14. Synthetic retirement tests preserve overlap, identify sources, reject false intervals and leave unsupported months unavailable.
- Calibration: frozen 2019-2021 geographic audits, 2024-2025 retrospective experiment, and training-only four-fold cross-validation are documented offline. No experimental model was promoted and no production Parquet or model artifact was replaced.
- Data quality: unknown VIIRS confidence fails future CSV normalization. Existing imported records were not rewritten.
- Output safeguards: residual ranges are approximate, zero-width ranges are unquantified, short baselines receive no anomaly label, and unskillful forecasts without sufficient climatology are withheld.
- Methods: 36-page Word report rendered with installed Microsoft Word and visually checked; all 19 native equations preserved. Packaged rendering could not locate LibreOffice; installed Word was used instead.

Existing prior browser checks covered live/archive switching, geometry selection, exports, summary, popups and mobile layout. This revision changed uncertainty wording, not page layout. Current code checks do not constitute user research, ground-truth fire validation or prospective scientific validation.

Repeat application checks with Verify.ps1. Offline experiments are explicit commands; they are not run by startup, and completed evaluations are not silently rerun. No new downloads, dependencies or automatic history updating were added.

Actual intended-user sessions and genuinely new observations remain external evidence needs. The methods report includes a ready-to-run user evaluation protocol; no user feedback or scientific results have been invented.

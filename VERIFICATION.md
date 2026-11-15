# Verification ? Australia app replacement, 15 November 2026

## Completed

- **53 backend tests passed.** Includes preserved original-data provenance and research/API regressions, disjoint model partitions, training-only learned features and platform scaling, Terra-only input checks, measured-reference uncertainty handling, fractional-area variance, native S-NPP independence from reference selection, unavailable-vs-zero years, partial-month anomaly handling, regional fallback, exports, archive retry/resume and scope validation.
- **5 frontend tests passed**, including leap-day/last-available-day handling, Australian bounds, baseline calculations and CSV fidelity.
- **TypeScript and Vite production build passed.** The final built app is in `frontend/dist`.
- **The replacement harmonization was run on the actual imported national data.** Outputs use `harm-v3.0-independent-test`, with 2,131,904 stored cell-month rows. Frozen final-test predictions were generated before production refitting. Native S-NPP support contains 739,718 nonempty cell-months and 15,889,987 quality-filtered records.
- **Forecasts were retrained** against the v3 record using the existing climate inputs. National CRPS skill is 0.026 / 0.0762 / 0.0987 at leads 1/2/3; regional skill is negative, and live selected-area checks confirmed climatology fallback for subnational selections. Version/build timestamps agree.
- **Real-data API checks passed** for all five series, actual-AOI metadata, model/reference interval distinctions, frozen test evidence, daily/hotspot query consistency and provenance-bearing calendar exports. Final live checks confirmed November native S-NPP counts of 5,390 remain distinct from the scaled reference, and checked all three national/regional forecast branches.
- **Browser checks passed** for national analysis, source-aware calendars and uncertainty labels, production and independent-test comparison, daily date selection/monthly native counts and capped selected-day mapping, rebuilt forecasts, and the new national model-validation page. The final production build was reloaded and visually inspected. Existing Revamp branding/layout and OSM rendering were retained.
- Previous model outputs and the previous forecast artifacts were backed up under `data/model_backups`. Imported raw Parquets and original `data/demo` CSVs were preserved. Original research screens/API endpoints remain available separately.

## Scientific results and limits

The selected main transfer is the global day/night ratio, with a LightGBM Terra-only early-period model. On final-test years 2019?2021, national yearly absolute error is 5.58% against the VIIRS reference, versus 79.33% for unadjusted MODIS. Nominal 80% residual-interval test coverage is 72.22% nationally and 85.99% for 0.5-degree regions. These values are displayed without converting them into a claim of ground-truth fire accuracy or tuning to the final-test outcomes.

No independent field/fire-perimeter validation or exhaustive browser matrix for every geometry/device was performed. Hotspot-only data do not provide cloud-free viewing exposure or recover all omitted fires. Daily observations remain native sensor records; monthly calibration is not claimed to be a validated daily model. Forecast skill is conditional on retrospectively calibrated history and remains experimental.

One upstream Starlette/AnyIO deprecation warning remains in the backend test suite.

Repeat code checks with `Verify.ps1`. Rebuild data with `Build-Australia.ps1 -Step harmonize`, followed by `-Step forecast`; existing climate outputs can be reused. Restart the API after rebuilding. See `docs/HARMONIZATION_V3.md` for the full measurement/validation contract.

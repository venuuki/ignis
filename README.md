# EmberCal

### Australia's fire history, on a common satellite reference scale

**NASA Space Apps Challenge 2026 · Harmonization of MODIS and VIIRS Hot Spots**  
**Team Ceylon Xzora**

EmberCal is an Australia-focused research prototype that makes historical MODIS and newer VIIRS hotspot counts more comparable. It brings a harmonized burning activity calendar, recent NASA FIRMS observations, and visual seasonal comparisons into one web application for land managers, fire analysts, and researchers.

## What you can do

- Explore Australia nationally or select a region, enter coordinates, use a point and radius, or draw an area on the map.
- Inspect recent NASA FIRMS hotspots over the last 24 hours, 72 hours, or seven days, including today's available detections.
- See the usual active season, peak month, and how unusual the latest complete month is against a selected historical baseline.
- Explore monthly fire activity, anomalies, seasonal patterns, complete-year comparisons, and native daily observations.
- Compare **Harmonized**, **MODIS**, and **VIIRS reference** series.
- Download matching hotspot records and analytical CSVs, calendar images, and visual summaries.

The website has two main workspaces: **Australia analysis** and **Observations**. Detailed methods and model validation are supplied as offline documentation.

## Run locally

Requirements: Windows PowerShell, Python 3.10 or newer, and Node.js 22 or newer. Initial setup downloads the required Python and frontend packages.

```powershell
cd D:\Desktop\IGNIS-Revamp
powershell -ExecutionPolicy Bypass -File .\Setup.ps1
powershell -ExecutionPolicy Bypass -File .\Start.ps1
```

If the environment and frontend build already exist, run only `Start.ps1`.

- Application: <http://127.0.0.1:8010>
- API documentation: <http://127.0.0.1:8010/docs>

`Start.ps1` recognizes an existing EmberCal server on port 8010. After rebuilding data or changing backend code, stop the original server with Ctrl+C and restart it. For frontend development, run `Start-Frontend.ps1`; port 5173 proxies requests to the API on port 8010.

## How harmonization works

MODIS and VIIRS have different pixel sizes, sensitivities, and observation schedules. Their raw detection counts cannot be compared directly.

1. Quality-filter the supplied satellite observations and assign detections to a shared **0.1-degree geographic grid**.
2. Aggregate counts by cell, month, and day/night observation class.
3. Learn the relationship between MODIS counts and the VIIRS S-NPP reference during overlapping years, using separate training, calibration, and final-test periods.
4. Express MODIS history on the reference count scale. The deployed main transfer uses separate day and night coefficients: approximately **3.44 × daytime MODIS + 8.62 × nighttime MODIS**. The early Terra-only period uses its own LightGBM model.
5. Aggregate the resulting cell estimates for the selected area and calculate baseline comparisons.

The coefficients are fitted relationships for this dataset, not universal sensor constants. Harmonized retains calibrated MODIS through the last supplied MODIS month and uses the VIIRS reference for subsequent continuity. The VIIRS reference can include calibrated platform substitution when S-NPP is unavailable. Missing observations are distinguished from observed zero counts.

Daytime and nighttime are treated separately because sensor detection behavior differs between them. The monthly model does not manufacture harmonized daily observations. See the technical methods report for exact formulas, reference construction, uncertainty, and spatial boundary weighting.

## Evidence and limits

The main evaluation separates training years **2013, 2016, 2018, 2023**, calibration years **2017, 2022**, and frozen final-test years **2019–2021**. Production fitting follows the frozen evaluation.

| Frozen final-test measure | Raw MODIS | Harmonized |
| --- | ---: | ---: |
| National monthly WAPE | 79.4% | 10.2% |
| Northern sample monthly WAPE | 78.4% | 13.5% |
| Southeastern sample monthly WAPE | 81.3% | 11.6% |
| Southwestern sample monthly WAPE | 79.3% | 21.5% |

WAPE is total absolute monthly error divided by total reference count. These results measure agreement with the VIIRS reference, not independently verified fire accuracy. Regional samples are not held-out geographic tests. The nominal 80% residual range achieved **72.2% national test coverage**, so uncertainty remains approximate.

- Hotspots are satellite detections, not unique fires, burned area, or a fire-spread forecast.
- Clouds, satellite passes, publication delays, and sensor omissions affect what is observed. A hotspot archive alone does not measure cloud-free viewing coverage.
- Incomplete months are excluded from full-month anomaly comparisons. Anomaly classifications require at least five baseline values.
- Historical baseline controls are bounded by available history and the current year.
- Experimental outlook and climate context are secondary features. Unskillful outlooks use a historical baseline where supported; insufficient history is withheld.
- Alternative calibration experiments remain offline and were not automatically promoted to production.
- User studies, independent fire validation, cloud-exposure accounting, and prospective evaluation remain future work.

## Historical data and live observations

The supplied archive contains **49 Australia Parquet files**, with **35,564,819 input records through 14 November 2026**. Import checksums and provenance are recorded in `data/australia_parquet_import.json`. These large data files must be available locally to reproduce this installed calendar; do not assume a source-only download includes them.

The live map retrieves near-real-time NASA FIRMS observations separately from the saved historical calendar. It refreshes while the page is visible, with five-minute source caches. Live detections retain native sensor counts and do not automatically update or retrain historical outputs.

Set `FIRMS_MAP_KEY` in the backend process environment or a local root/backend `.env` file for keyed FIRMS access. Without a key, the app uses NASA's public regional feeds when available. Credentials must remain server-side and must never be committed or placed in frontend variables. Imported historical data can be viewed without a FIRMS key. Live feeds and OpenStreetMap tiles need internet access; a bundled Natural Earth map is available as a fallback.

## Rebuild the saved outputs

Use the supplied imported archive directly; ingestion is not required merely to open it.

```powershell
powershell -ExecutionPolicy Bypass -File .\Build-Australia.ps1 -Step harmonize
powershell -ExecutionPolicy Bypass -File .\Build-Australia.ps1 -Step forecast
```

Run `-Step drivers` only when climate inputs need rebuilding. `-Step ingest` contacts NASA and can require substantial downloads; it is a deliberate pipeline operation. Restart the backend after rebuilding because outputs are cached. Forecasts must match the current harmonization version and build timestamp; stale outputs are withheld.

## Project structure

| Path | Purpose |
| --- | --- |
| `frontend/` | React, TypeScript, Vite, MapLibre interface and exports |
| `backend/` | FastAPI services, area analysis, calendar and live hotspot retrieval |
| `pipeline/` | Historical processing, calibration, climate drivers and experimental outlooks |
| `data/` | Local input archive, processed outputs and audit artifacts |
| `docs/` | Technical methods, source continuity and evaluation evidence |
| `submission/` | Final seven-slide demo, documentation and submission form text |

Run the project checks with:

```powershell
powershell -ExecutionPolicy Bypass -File .\Verify.ps1
```

The latest recorded implementation verification passed **82 backend tests, 9 frontend tests, and the TypeScript/Vite build**. See [VERIFICATION.md](VERIFICATION.md) for verification context.

## Documentation and submission

- [Project documentation](submission/EmberCal_Project_Documentation.docx)
- [Seven-slide demonstration](submission/EmberCal_Seven_Slide_Demo.pptx)
- [Copy-paste submission details and resource credits](submission/COPY_PASTE_SUBMISSION.txt)
- [Full calibration and data methods](docs/EmberCal_Calibration_and_Data_Methods.docx)
- [Sensor continuity](docs/Sensor_Continuity.md)
- [Frozen regional validation](docs/regional-validation/README.md)
- [Calibration experiment assessment](docs/calibration-experiment/ASSESSMENT.md)
- [Training-only cross-validation](docs/calibration-cross-validation/README.md)

Submission materials include the team, actual interface images, measured results, limitations, and SI assistance disclosure. Public demo and project hosting URLs must be supplied separately; local addresses are not public submission links.

## Data sources and acknowledgments

NASA FIRMS provides MODIS and VIIRS active-fire observations. NASA POWER and NOAA climate indices support the separate climate/outlook components. OpenStreetMap contributors and Natural Earth provide mapping resources. Full source URLs, research references, software credits, and provenance details are included in the documentation and submission text.

EmberCal was developed with SI assistance from **OpenAI ChatGPT and Codex** for coding, analysis, documentation, and submission preparation. The project also uses locally fitted machine-learning models as documented. No synthetic satellite observations are presented as real measurements. The team remains responsible for reviewing the work and its claims.

**Ceylon Xzora:** Vinudhi Wahalathanthri · Sinura Wahalathanthri · Venuki Weerakoon · Pulesh Aponso · Tharaka Tharupathi

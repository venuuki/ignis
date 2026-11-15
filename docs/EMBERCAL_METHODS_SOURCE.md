# How EmberCal Works: Code, Maths and Methods

This document explains, step by step, what the code does and the maths behind each step. It also covers how the Román et al. (2024) MODIS→VIIRS continuity review maps onto our harmonization, and what is planned next.

---

## 0. The big picture

```
 FIRMS hotspots (points)            NASA POWER + ENSO/IOD
        │                                   │
  ① ingest_firms.py                  ③ drivers.py
  clean + grid to 0.1° cells          monthly climate anomalies
        │                                   │
  ② harmonize.py ───────────────► ④ forecast.py
  MODIS → VIIRS-equivalent            1–3 month probabilistic forecast
  monthly counts + uncertainty              │
        │                                   │
        └────────────► backend (FastAPI) ◄──┘
                       sums cells inside the user's area,
                       computes calendar / anomalies / trend / forecast
                              │
                       frontend (React): map, calendar, charts, downloads
```

| File | What it does |
|---|---|
| `pipeline/common.py` | Shared helpers: grid maths, FIRMS field normalisation, quality flags |
| `pipeline/ingest_firms.py` | Downloads the FIRMS archive and recent data, cleans it, writes `hotspots.parquet` |
| `pipeline/harmonize.py` | Builds the cell × month table, trains and validates the MODIS→VIIRS models, writes `monthly_cells.parquet` |
| `pipeline/drivers.py` | NASA POWER climate (soil wetness, rain, temperature, humidity, wind), ENSO and IOD |
| `pipeline/forecast.py` | Forecast model, back-test, live forecast |
| `backend/app/aoi.py` | Turns the user's area into weighted grid cells |
| `backend/app/analytics.py` | Calendar, anomalies, season, trend and forecast maths |
| `backend/app/main.py` | API endpoints, CSV exports |
| `frontend/src/*` | User interface |

---

## 1. Cleaning and gridding (`ingest_firms.py`, `common.py`)

Each FIRMS detection is a pixel flagged as containing a fire at overpass time. We keep a detection only if all of these hold:

- **Vegetation fire:** FIRMS `type = 0`. Volcanoes (1), static industrial sources (2) and offshore platforms (3) are removed. Near-real-time data has no `type` field, so it is cleaned with a mask of every ~1 km location ever flagged as a static source in the 25-year archive.
- **Confidence:** MODIS ≥ 30%; VIIRS "nominal" or "high".
- **Not a duplicate:** same place, time and satellite.

Each detection is then assigned to a 0.1° grid cell (about 11 km):

```
row  = ⌊(lat + 90) / 0.1⌋          col = ⌊(lon + 180) / 0.1⌋
cell = row · 3600 + col
```

There is also a coarser 0.5° "region" cell, the same grid as NASA's MODIS Climate Modelling Grid fire product.

---

## 2. Why MODIS and VIIRS counts differ

A satellite flags a pixel when the fire inside it raises the pixel's mid-infrared signal above the background. The fire therefore has to fill a large enough fraction of the pixel to be seen.

| | MODIS | VIIRS I-band |
|---|---|---|
| Pixel size at nadir | ~1 km × 1 km = **1 km²** | 375 m × 375 m ≈ **0.14 km²** |
| Smallest detectable fire | larger | **about 7× smaller** |
| One large fire appears as | 1 pixel | several pixels |

So for the same burning, VIIRS sees **(a) more small fires** and **(b) more pixels per large fire**. For one cell and month:

```
E[N_VIIRS] = Σ_fires  P_V(detect | size, FRP, cloud, time) · pixels_V(fire)
E[N_MODIS] = Σ_fires  P_M(detect | size, FRP, cloud, time) · pixels_M(fire)
```

The ratio `N_VIIRS / N_MODIS` is **not a constant**. It depends on the fire-size distribution, which changes with land cover (crop stubble vs savanna vs forest), season, and day vs night. In our Australian data the average ratio is about **5×**, but it varies a lot from cell to cell and month to month. That is why a single scaling factor gives 23% yearly error, while a model that sees fire intensity and context gives 12%.

---

## 3. The harmonization (`harmonize.py`)

**Goal:** express every month since Nov 2000 in one unit, *VIIRS S-NPP-equivalent hotspots*. We convert MODIS to VIIRS rather than the other way round because VIIRS is the record that continues; MODIS is ending.

### 3.1 VIIRS reference series (the "truth" we learn to predict)

S-NPP is the reference. When S-NPP has a gap (it has no near-real-time data in parts of 2026), NOAA-20 or NOAA-21 fills in, scaled by a ratio learned where both overlap. The ratio is shrunk towards the global value when data are scarce:

```
r_country = (Σ S-NPP + k · r_global) / (Σ NOAA-20 + k),   k = 50
```

The ratios found are 0.96–0.99, in line with the 2–6.5% S-NPP/NOAA-20 calibration differences reported by Román et al. (2024).

### 3.2 Training data: the overlap period

From 2012 to 2025 both sensors observed the same fires. For every cell and month we know the MODIS observation (inputs) and the VIIRS count (target, `y`). That gives about 300,000 training examples.

Two adjustments:
- **Held-out years 2019–2021** are kept out of training and used only for testing.
- **Orbit drift:** Terra and Aqua have drifted in recent years. Terra reached a 10:15 overpass in Sept 2022 and is heading towards 9:00 (Román et al., 2024). Years from 2021 on get half weight, so the model learns the stable-orbit relationship that applies to 2000–2011.

### 3.3 The candidate models

**(a) No harmonization:** `ŷ = MODIS count`. This is the baseline that shows the problem.

**(b) Global ratio**, computed separately for day (d) and night (n):
```
R_c = Σ y_c / Σ m_c          ŷ = R_d · m_d + R_n · m_n
```

**(c) Hierarchical empirical-Bayes ratios.** Each cell gets its own ratio, but a cell with few fires borrows strength from its region, country and season:
```
y_c = b(cell, month) + R(cell, month) · m_c
R   = R_global · f_country×month · f_region · f_cell
f   = (Σ (y − b) + k) / (Σ R_parent · m + k)          (k = 30 pseudo-detections)
```
This is the posterior mean of a Gamma–Poisson model. With few fires, `f → 1` (use the parent's ratio); with many fires, `f →` the cell's own ratio. `b` is the "small fires MODIS misses entirely" baseline: the expected VIIRS count in cell-months where MODIS saw zero.

**(d) Gradient-boosted trees (LightGBM), the selected model.** The model predicts the expected VIIRS count `μ` from 17 inputs `x`:

- Terra and Aqua day/night counts, MODIS FRP sum and maximum;
- 0.5° neighbourhood activity, country activity per cell;
- the cell's long-term MODIS fire climatology (overall and for this month);
- month (as sin/cos), latitude/longitude, country.

```
log μ(x) = F(x) = Σ_{t=1..T} η · tree_t(x)
```
Each new tree is fitted to the gradient of the loss left by the previous trees ("boosting"), with learning rate η = 0.04. The number of trees T is chosen by early stopping on a validation year.

The loss is the **Tweedie deviance** with power p = 1.35. It suits counts that are mostly zero, with a long tail of huge months:
```
L(y, μ) = −y · μ^(1−p)/(1−p) + μ^(2−p)/(2−p)
```
A Tweedie distribution with 1 < p < 2 is a compound Poisson–gamma: it has a point mass at zero plus a skewed positive part, which is exactly what monthly fire counts look like.

The most important inputs turn out to be **MODIS FRP** (31%) and the **cell's monthly fire climatology** (27%). FRP measures fire intensity and size, which is exactly the physical quantity in §2 that controls how many extra VIIRS pixels a fire produces.

**(e) Ensemble:** the average of (c) and (d).

### 3.4 Model selection (held-out years 2019–2021, incl. Black Summer)

Prototype region: **Australia** (≈33 million detections). Training uses a sample of overlap years (2013, 2016, 2018, 2023) plus a 15% re-weighted sample of empty cell-months. The held-out years use every cell-month.

| Model | Yearly error | Monthly error | Bias |
|---|---|---|---|
| (a) Raw MODIS | 79.3% | 79.3% | −79.4% |
| (b) Global ratio | 5.6% | 11.7% | −4.4% |
| (c) Empirical Bayes | 8.8% | 12.4% | +8.7% |
| **(d) LightGBM** | **2.2%** | **6.6%** | **−2.1%** |

The selected model is refitted on the sampled overlap years and applied to every cell-month from Jul 2002 to Dec 2011. A **Terra-only** version covers Nov 2000–Jun 2002 (5.2% yearly error). The most important inputs are the MODIS **fire radiative power** sum and maximum, the physical quantity that controls how many extra VIIRS pixels a fire produces.

### 3.5 Uncertainty for every modelled month

Each cell-month's prediction gets a variance, fitted on held-out residuals:
```
Var(y | μ) = φ · μ + α · μ²          (φ ≈ 10, α ≈ 0.09)
```
For a user's area with cell weights `w_i` (the fraction of each cell inside the area):
```
Ŷ = Σ w_i μ_i          80% interval = Ŷ ± 1.2816 · κ · √(Σ w_i Var_i)
```
Errors in neighbouring cells are correlated (a fire season that is unusual for the model is unusual everywhere), so summing variances alone would make the interval too narrow. `κ` is calibrated on held-out data so that 80% of true values fall inside: κ ≈ 1.0 for a 0.5° region and ≈ 2.1 for a country.

### 3.6 Proof that the sensor jump is gone: Pettitt change-point test

For a monthly series `x₁…xₙ` (log counts with the seasonal cycle removed):
```
U_t = Σ_{i≤t} Σ_{j>t} sign(x_i − x_j)        K = max_t |U_t|
p ≈ 2 · exp(−6K² / (n³ + n²))
```
If splicing MODIS to VIIRS creates a step, the test finds it at Jan 2012 with tiny p. Results:
- **Australia:** naive ×5.24; harmonized ×1.16, with the remaining change point at **2011-02**. That's the record 2010–11 La Niña and the huge central-Australian grass fires that followed, not the sensor switch.

---

## 4. Calendar and anomaly maths (`analytics.py`)

For the user's area: `Value(year, month) = Σ w_i · harmonized_i`.

For each calendar month `m`, the baseline values are the counts of month `m` in the baseline years (default 2003–2022).

| Quantity | Formula |
|---|---|
| Median, P10…P90 | quantiles of the baseline values |
| % of median | `100 · x / median_m` |
| Percentile rank | `100 · (#{v < x} + ½ #{v = x}) / n` |
| Robust z-score | `(x − median_m) / (1.4826 · MAD_m)`, where 1.4826·MAD is a standard deviation that ignores outliers |
| Label | < 10 much below · 10–33 below · 33–67 near · 67–90 above · ≥ 90 much above · above every past year = record high |
| Partial month | `projected = count so far / fraction of month observed`; no label below 35% coverage |
| Fire season | months whose median ≥ 20% of the peak month, contiguous around the peak; "critical" = top quartile of months |
| Trend | Mann–Kendall `S = Σ_{i<j} sign(x_j − x_i)` (is there a monotonic trend?), Sen's slope = median of all pairwise slopes `(x_j − x_i)/(j − i)`; both are robust to outlier years |

---

## 5. Forecast maths (`forecast.py`)

**Question:** given everything known at the end of month `t₀`, what is the distribution of the hotspot count in month `t₀ + L`, for L = 1, 2, 3?

### 5.1 Model
```
log μ = log(c) + F(x)
```
- `c` is the prior-years climatology for the target month: the cell's average count for that calendar month, using **only earlier years**, so the future never leaks into training. It is blended 80/20 with the 0.5° region average.
- `F(x)` is a LightGBM Tweedie model. It learns only **departures from normal**: "given dry soils, low rain, El Niño and an already-busy season, expect ×1.4 of normal".

If the drivers carry no information, `F → 0` and the forecast falls back to climatology. This offset is what moved skill from slightly worse than climatology to better than climatology.

Inputs `x`:
- **Fire history:** counts at t₀, t₀−1, t₀−2; 12-month total; same month last year; the current anomaly; neighbourhood activity.
- **NASA POWER (MERRA-2):** root-zone soil wetness and its anomaly; 1- and 3-month rain anomaly; temperature, maximum temperature, humidity and wind anomalies.
- **Climate modes:** ENSO (ONI) and IOD (DMI), lagged to what is published at issue time.
- **Other:** season, location, lead time.

### 5.2 From a single number to a probability
For an area, `μ_area = Σ w_i μ_i`. Monthly fire counts are over-dispersed: they vary far more than a Poisson distribution allows. We therefore use a **negative binomial** distribution:
```
Y ~ NegBin(mean = μ, variance = μ + α μ²)
```
`α` is fitted by **maximum likelihood** on back-test aggregates, separately for cell, region and country size. From this distribution:
- P10 / P50 / P90 give the 80% range;
- P(Y ≤ lower tercile), P(between), P(Y > upper tercile) give the below/near/above-normal outlook.

### 5.3 Honest testing: rolling-origin back-test
For each year Y from 2017 to 2025: train only on data before Y, then forecast every month of Y. Scores:
```
CRPS(F, y) = Σ_k (F(k) − 1{k ≥ y})²              (penalises both a wrong centre and a wrong spread)
CRPSS      = 1 − CRPS_model / CRPS_climatology    (> 0 means better than "assume a normal year")
```
**Result (back-test 2023–2025), all of Australia:** +3% (1 month), +8% (2 months), +11% (3 months) better than climatology. Australia's interior fires follow rain from 6–18 months earlier, so 6- and 12-month rainfall and soil-wetness anomalies are inputs; 12-month rain is the top climate driver. For smaller 0.5° areas the model doesn't yet beat climatology. **Safety rule:** wherever the model has not beaten climatology in back-tests, the app shows the historical range for that month and says so.Since v2 the forecast model runs on 0.5° regions (less noise, about 3,100 regions). Region forecasts are then shared out to 0.1° cells in proportion to each cell's historical share of the region's fires in that calendar month, so any drawn area can still be summed.

### 5.4 Why we don't promise "high accuracy" for forecasts
Monthly fire activity depends on ignitions (people, lightning) and on weather weeks ahead, which nobody can predict. Operational seasonal fire outlooks typically show modest skill too. What we guarantee is that forecasts are **calibrated** (an 80% range contains the truth about 80% of the time) and **never worse than climatology**, because the model is anchored to it. Skill grows with:
- more training regions with varied fire regimes (see §7);
- stronger drought and climate signals: Fire Weather Index, GEOS-S2S seasonal forecasts;
- the current FIRMS data, which makes the live forecast start from last month.

---

## 6. What the Román et al. (2024) continuity review tells us, and how we use it

The paper reviews how NASA's land team makes VIIRS products continue MODIS products. It mostly covers reflectance, temperature, vegetation and burned area, not active-fire counts, but its continuity principles carry over:

| Paper finding / method | How it maps to EmberCal | Status |
|---|---|---|
| **Aqua MODIS is the reference** for VIIRS because both cross at ~13:30. Terra (10:30 AM) has no VIIRS equivalent. | Aqua and Terra are separate model inputs; a Terra-only model covers pre-Aqua months. | ✅ done |
| **Cross-calibrate on the overlap period** against a reference, then apply. | Overlap 2012–2025 is the training set; the conversion is learned there and applied backwards. | ✅ done |
| They recalibrated the **shorter** record (VIIRS) to MODIS, to avoid reprocessing 20 years. | We convert MODIS→VIIRS because VIIRS is the future record (MODIS ends ~2026–27). A "MODIS-equivalent" view (the inverse map) can be added for users who want the paper's convention. | ➕ optional |
| **Time-dependent calibration** `y' = y · (A + B·(Year − C))`, with a small trend term (~0.1%/yr). | Add a year term and check that the VIIRS/MODIS ratio residuals show no trend over 2012–2025; this guards against slow sensor degradation. | 🔜 planned |
| **SNPP vs NOAA-20 differ by 2–6.5%** before cross-calibration. | NOAA-20/21 are scaled to S-NPP (ratios 0.96–0.99). | ✅ done |
| **Orbital drift:** Terra at 10:15 by Sept 2022, ~9:00 by 2026; Aqua also drifting. | Drift years down-weighted from 2021. Next step: exclude Terra after Sept 2022 from training and use overpass time (from `acq_time`) as an input. | 🔜 refine |
| Burned-area continuity is **excellent in savannas (r = 0.994) but weaker in croplands (r = 0.963)**, partly because there is no morning VIIRS overpass to see morning crop burning. | The VIIRS:MODIS relationship depends on fire type, so we must stratify by land cover/biome. Training on Africa (savanna) and Australia (savanna/forest), plus South Asia (cropland/forest), lets the model learn each regime. | 🔜 with new regions |
| **Bias correction per land-cover type** (VNP17 recalibrates parameters per biome). | Add MCD12Q1 land-cover class as a model input and report accuracy per biome. Needs an Earthdata login. | 🔜 planned |
| Validation via **OLS on ~120 km cells monthly** (Pearson r) and **independent reference data** (Landsat burned area). | We already report region-month and country-month r. Next: compare harmonized counts with **MCD64A1/VNP64A1 burned area** as an independent check. | 🔜 planned |
| Afternoon **cloud cover** causes regression to climatology. | Clouds hide fires from both sensors; the cloud-corrected MOD14CMH product can be used as a reference check. | 🔜 optional |

---

## 7. Plan: training on Australia and Africa

**Why it helps:** Africa produces most of the world's burned area, and northern Australia's savannas are among the most fire-prone landscapes on Earth. Adding them:
- multiplies the overlap training data roughly 50–100×;
- covers the savanna, woodland and forest fire regimes the paper says behave differently;
- gives the forecast model many more fire seasons to learn drought and ENSO responses from (ENSO strongly affects both Australia and East Africa).

**Data size (FIRMS archive, per year, S-NPP only):** Australia ≈ 130–150 MB; Sudan 17 MB; South Sudan 52 MB; Central African Republic 43 MB; DR Congo 144 MB; Angola 111 MB; Zambia 69 MB. All of Africa with MODIS + S-NPP + NOAA-20 for 2000–2025 is **tens of GB**, too much for a laptop pipeline. Proposed staged scope:

1. **Stage 1:** Australia, Sudan, South Sudan, Central African Republic (≈ 5–6 GB raw, converted to compressed Parquet as downloaded).
2. **Stage 2 (optional):** more of the African savanna belt (Angola, Zambia, DR Congo, Mozambique, Nigeria…), as disk and time allow.

**Code changes needed:**
- stream each yearly CSV → Parquet and delete the CSV, to keep disk use low;
- for big regions, build the dense table per country and train the harmonization on a stratified sample;
- replace the "country" input with fire-regime inputs (mean FRP, day/night share, seasonality), so the model generalises across countries;
- report validation per country and per regime;
- add per-region fire calendars to the region picker.

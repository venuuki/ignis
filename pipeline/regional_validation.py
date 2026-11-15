"""Offline regional checks of frozen predictions; no downloads or model fitting.

Run from project root: .venv/Scripts/python -m pipeline.regional_validation
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
import polars as pl
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from embercal_engine.aoi import cell_weights
from embercal_engine.schemas import AOI
from embercal_engine.analytics import _interp_log, Z80

# Fixed geographic samples, not vegetation masks or administrative boundaries.
REGIONS = [
    ("australia", "Australia", None),
    ("north", "Northern Australia sample", [129., -17., 137., -12.]),
    ("southeast", "Southeastern Australia sample", [146., -38., 151., -32.]),
    ("southwest", "Southwestern Australia sample", [115., -35., 120., -30.]),
]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def errors(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    total = y.sum()
    return dict(monthly_mae=float(np.abs(p-y).mean()),
                wape_pct=float(100*np.abs(p-y).sum()/total) if total else None,
                bias_pct=float(100*(p-y).sum()/total) if total else None)


def transitions(rows):
    """Every adjacent held-out pair; never bridge non-consecutive months."""
    result = []
    for prev, cur in zip(rows, rows[1:]):
        if cur["year"]*12+cur["month"] != prev["year"]*12+prev["month"]+1:
            continue
        reference_jump = cur["reference"]-prev["reference"]
        switched_jump = cur["reference"]-prev["prediction"]
        result.append(dict(switch_month=f'{cur["year"]}-{cur["month"]:02d}',
            previous_prediction=prev["prediction"], previous_reference=prev["reference"],
            next_reference=cur["reference"], reference_jump=reference_jump,
            switched_jump=switched_jump, excess_jump=switched_jump-reference_jump))
    return result


def anomaly_check(rows, key):
    # Common VIIRS baseline for both methods, never fitted to predictions.
    # Two peer years are too few for operational percentiles; diagnostic only.
    scores = []
    for row in rows:
        peer = [r["reference"] for r in rows if r["month"] == row["month"] and r["year"] != row["year"]]
        if len(peer) < 2:
            continue
        baseline = float(np.median(peer))
        if baseline <= 0:
            continue
        category = lambda x: -1 if x < .8*baseline else 1 if x > 1.2*baseline else 0
        scores.append(category(row[key]) == category(row["reference"]))
    return dict(n_months=len(scores), agreement_pct=100*float(np.mean(scores)) if scores else None)


def evaluate(d, effective_cells, vm):
    rows = d.to_dicts()
    y, p, raw = (d[c].to_numpy() for c in ("reference", "prediction", "modis_raw"))
    k = _interp_log(max(effective_cells, 1.), [(1,1.), (25,vm.get("kappa_region",1.)), (1000,vm.get("kappa_country",1.))])
    radius = Z80*k*np.sqrt(d["variance"].to_numpy())
    season = d.group_by("month").agg(pl.col("reference").mean(),pl.col("prediction").mean(),pl.col("modis_raw").mean()).sort("month")
    annual_peaks = []
    annual_metrics = []
    for year in sorted(d["year"].unique().to_list()):
        year_rows = [r for r in rows if r["year"] == year]
        peak = lambda key: max(year_rows, key=lambda r:r[key])["month"]
        annual_metrics.append(dict(year=year,
            harmonized=errors([r["reference"] for r in year_rows],[r["prediction"] for r in year_rows]),
            modis=errors([r["reference"] for r in year_rows],[r["modis_raw"] for r in year_rows])))
        ref = peak("reference")
        model, naive = peak("prediction"), peak("modis_raw")
        distance = lambda m: min(abs(m-ref),12-abs(m-ref))
        annual_peaks.append(dict(year=year,reference_peak=ref,harmonized_peak=model,modis_peak=naive,
            harmonized_peak_distance_months=distance(model),modis_peak_distance_months=distance(naive)))
    cuts = transitions(rows)
    typical = float(y.mean())
    extra = np.array([r["excess_jump"] for r in cuts])
    model_metrics, raw_metrics = errors(y,p), errors(y,raw)
    result = dict(months=d.height,raw_modis=raw_metrics,harmonized=model_metrics,
        wape_reduction_percentage_points=(raw_metrics["wape_pct"]-model_metrics["wape_pct"]) if y.sum() else None,
        interval_coverage_80_pct=100*float(np.mean(np.abs(p-y)<=radius)),
        seasonal_profile_spearman=dict(harmonized=float(spearmanr(season["reference"],season["prediction"]).statistic),
            modis=float(spearmanr(season["reference"],season["modis_raw"]).statistic)),
        annual_peaks=annual_peaks, annual_metrics=annual_metrics,
        exploratory_anomaly_agreement=dict(harmonized=anomaly_check(rows,"prediction"),modis=anomaly_check(rows,"modis_raw")),
        transitions=dict(n=len(cuts),mean_excess_jump=float(extra.mean()) if len(extra) else None,
            mean_abs_excess_jump=float(np.abs(extra).mean()) if len(extra) else None,
            mean_abs_excess_pct_typical_month=float(100*np.abs(extra).mean()/typical) if len(extra) and typical else None,
            max_abs_excess_pct_typical_month=float(100*np.abs(extra).max()/typical) if len(extra) and typical else None))
    return result, cuts, season


def render_figures(series, results, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":10,"axes.spines.top":False,"axes.spines.right":False})
    colors={"reference":"#192f38","prediction":"#337d76","modis_raw":"#ba713f"}
    fig, axes = plt.subplots(4,2,figsize=(14,14),layout="constrained")
    for (identifier,name,_), ax in zip(REGIONS,axes):
        d=series[identifier]
        for key,label in [("reference","VIIRS reference"),("prediction","Harmonized — frozen test"),("modis_raw","MODIS")]:
            ax[0].plot(np.arange(d.height),d[key],label=label,color=colors[key],lw=1.5,ls="--" if key=="modis_raw" else "-")
        ax[0].set(title=name,ylabel="Monthly hotspot count")
        ticks=list(range(0,d.height,6));ax[0].set_xticks(ticks,[f'{d["year"][i]}-{d["month"][i]:02d}' for i in ticks],rotation=30)
        if identifier=="australia":ax[0].legend(fontsize=8)
        seasonal=d.group_by("month").agg(pl.col("reference").mean(),pl.col("prediction").mean(),pl.col("modis_raw").mean()).sort("month")
        for key in colors:ax[1].plot(seasonal["month"],seasonal[key],color=colors[key],lw=1.5,marker="o",markersize=3)
        ax[1].set(title="Seasonal profile · 3 test years only",ylabel="Mean monthly count")
        ax[1].set_xticks(range(1,13),MONTHS,rotation=45)
        for a in ax:a.grid(alpha=.15)
    fig.suptitle("EmberCal · frozen temporal holdouts, 2019–2021\nGeographic samples; reference-sensor agreement, not field truth",fontsize=16)
    fig.savefig(out/"regional-series.png",dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(15,5),layout="constrained")
    names=[r["name"].replace(" Australia sample","") for r in results]
    x=np.arange(len(results))
    for offset,key,label,color in [(-.17,"raw_modis","MODIS","#ba713f"),(.17,"harmonized","Harmonized","#337d76")]:
        axes[0].bar(x+offset,[r[key]["wape_pct"] for r in results],.34,label=label,color=color)
    axes[0].set(title="Monthly count error · lower is better",ylabel="WAPE (%)");axes[0].legend()
    axes[1].bar(x,[r["interval_coverage_80_pct"] for r in results],color="#337d76")
    axes[1].axhline(80,color="#ba713f",ls="--",label="Nominal 80%")
    axes[1].set(title="Residual interval coverage",ylabel="Held-out months covered (%)",ylim=(0,105));axes[1].legend()
    axes[2].bar(x,[r["transitions"]["mean_abs_excess_pct_typical_month"] for r in results],color="#657e9e")
    axes[2].set(title="Simulated handoff · lower is better",ylabel="Mean absolute excess jump\n(% of typical monthly reference count)")
    for a in axes:a.set_xticks(x,names,rotation=35,ha="right");a.grid(axis="y",alpha=.15)
    fig.suptitle("Regional evidence and remaining limitations · 2019–2021",fontsize=15)
    fig.savefig(out/"regional-scorecard.png",dpi=160);plt.close(fig)


def main():
    data=ROOT/"data/processed";out=ROOT/"docs/regional-validation";out.mkdir(parents=True,exist_ok=True)
    report=json.loads((data/"harmonization_report.json").read_text())
    train,cal,test=(set(report[k]) for k in ("training_years","calibration_years","holdout_years"))
    if train & cal or train & test or cal & test:raise ValueError("Evaluation years overlap")
    path=data/"validation_cells.parquet"
    frozen=pl.read_parquet(path)
    if set(frozen["year"].unique().to_list()) != test:raise ValueError("Frozen years do not match test report")
    cells=pl.read_parquet(data/"cells.parquet")
    results=[];series={};cut_rows=[];monthly_rows=[]
    for identifier,name,bbox in REGIONS:
        aoi=AOI(type="bbox",bbox=bbox) if bbox else AOI(type="region",region="Australia")
        weights,summary=cell_weights(aoi,cells)
        d=frozen.join(weights,on="cell").group_by("year","month").agg(
            **{c:(pl.col(c)*pl.col("w")).sum() for c in ("reference","prediction","modis_raw")},
            variance=(pl.col("variance")*pl.col("w")**2).sum()).sort("year","month")
        expected={(y,m) for y in test for m in range(1,13)}
        if set(zip(d["year"],d["month"])) != expected:raise ValueError(f"Incomplete test months for {identifier}")
        result,cuts,_=evaluate(d,summary["effective_cells"],report["variance_model"]["full"])
        result.update(id=identifier,name=name,bbox=bbox,effective_cells=summary["effective_cells"])
        results.append(result);series[identifier]=d
        cut_rows.extend(dict(region=identifier,**row) for row in cuts)
        monthly_rows.extend(dict(region=identifier,**row) for row in d.to_dicts())
    payload=dict(generated_at=datetime.now(timezone.utc).isoformat(),
        evaluation_type="frozen_temporal_holdout_geographic_subsets",years=sorted(test),
        source_model_version=report["method_version"],source_built_at=report["built_at"],
        validation_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        region_selection="Fixed rectangles chosen before scoring; not biome masks or spatial holdouts.",
        anomaly_policy="Exploratory leave-one-year-out reference median, +/-20% band, 2 peer years; zero baseline excluded.",
        transition_policy="Every consecutive held-out month pair; calibrated MODIS before boundary, reference after. Excess jump = switched change minus reference-only change. This exposes residual calibration error, not prospective operational accuracy.",
        regions=results)
    (out/"results.json").write_text(json.dumps(payload,indent=2,allow_nan=False),encoding="utf-8")
    pl.DataFrame(monthly_rows).write_csv(out/"monthly.csv")
    pl.DataFrame(cut_rows).write_csv(out/"transitions.csv")
    render_figures(series,results,out)
    lines=["# Australian regional validation", "", "Frozen temporal holdouts: 2019, 2020, 2021. No model refitting, downloads or operational data changes.","",
        "![Regional scorecard](regional-scorecard.png)","", "| Geographic sample | MODIS WAPE | Harmonized WAPE | Harmonized bias | 80% interval coverage | Mean excess handoff jump / typical month |", "|---|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f'| {r["name"]} | {r["raw_modis"]["wape_pct"]:.1f}% | {r["harmonized"]["wape_pct"]:.1f}% | {r["harmonized"]["bias_pct"]:+.1f}% | {r["interval_coverage_80_pct"]:.1f}% | {r["transitions"]["mean_abs_excess_pct_typical_month"]:.1f}% |')
    lines += ["", "## Findings", ""]
    for r in results:
        misses = []
        for v in r["annual_peaks"]:
            if v["harmonized_peak_distance_months"]:
                misses.append('{}: {} reference versus {} harmonized'.format(v['year'],MONTHS[v['reference_peak']-1],MONTHS[v['harmonized_peak']-1]))
        lines.append('- {}: peak-month mismatches: {}. Seasonal rank correlation: {:.3f} harmonized versus {:.3f} MODIS.'.format(
            r['name'], '; '.join(misses) or 'none in these test years',
            r['seasonal_profile_spearman']['harmonized'], r['seasonal_profile_spearman']['modis']))
    worst = max(results, key=lambda r:r['harmonized']['wape_pct'])
    lines += ["", 'Largest remaining monthly count error: {} ({:.1f}% WAPE).'.format(worst['name'],worst['harmonized']['wape_pct']),
        "", "Count agreement improves in every sample, but seasonal shape is not uniformly improved. High interval coverage can mean overly broad intervals; low coverage means undercoverage. The simulated transition retains non-zero residual errors, so this is not proof of a seamless handoff. See individual and maximum handoff errors in results.json and transitions.csv.",
        "", "Regional or seasonal model changes must be selected using training/calibration data, rather than optimizing these now-inspected test years. A further untouched evaluation is needed before claiming new improvements generalize."]
    lines += ["", "## Scope", "", "WAPE is sum of absolute monthly errors divided by sum of reference counts. Bias is signed error divided by reference total. Geographic boxes and effective grid-cell weights are recorded in results.json. These are broad geographic samples, not independently verified vegetation classes. Test years were excluded from training, selection and interval fitting; these geographic locations were not excluded from training.", "", "## Season and anomaly diagnostics", "", "regional-series.png shows all 36 test months and their three-year mean seasonal profile. results.json records annual peak months, cyclic peak error and seasonal rank correlation. Ties select the earliest peak. Anomaly agreement uses the other two test years' same-month reference median and a +/-20% band, excluding zero medians. The band is a diagnostic convention, not an established hazard threshold. Two peer years cannot validate operational percentiles or the application's multi-decade baseline.", "", "## Simulated sensor transition", "", "transitions.csv tests every adjacent held-out pair, including adjacent test-year boundaries. It compares a frozen MODIS prediction before the cut with VIIRS reference after the cut. Subtracting the reference-only change isolates the residual handoff error. Reference values are reused after the cut by design; that does not independently validate VIIRS or establish future performance. No smoothing is applied to hide a discontinuity. The typical-month denominator is each region's mean reference count across the 36 months.", "", "## Limits and interpretation", "", "Lower reference agreement error is useful but is not ground-truth fire detection accuracy. Cloud obscuration, viewing opportunities, missed fires, independent field/burned-area validation and spatially withheld evaluation remain unresolved. Small regional totals make percentage errors sensitive to individual months. Nominal 80% interval coverage must be assessed region by region, not assumed. No multiple-comparison significance or win/loss claims are made from these three years.", "", "## Reproduce", "", "Run `.venv/Scripts/python -m pipeline.regional_validation` from the project root. This overwrites only docs/regional-validation outputs. SHA-256 of the frozen source is stored in results.json. The current production calendar and model files are read-only inputs.", "", "![Monthly comparisons](regional-series.png)"]
    (out/"README.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps(results,indent=2))
    print(f"Report: {out/'README.md'}")


if __name__ == "__main__":main()

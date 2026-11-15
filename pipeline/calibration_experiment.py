"""Offline regional ratio experiment. Never writes production data or calls APIs."""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import sys
import numpy as np
import polars as pl
from scipy.optimize import nnls

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from pipeline import harmonize as h
from pipeline.regional_validation import REGIONS, errors, transitions
from embercal_engine.aoi import cell_weights
from embercal_engine.schemas import AOI
from embercal_engine.analytics import _interp_log,Z80

TRAIN=[2013,2016,2018,2023]
SELECT=[2017]
INTERVAL=[2022]
TEST=[2024,2025]
OUT=ROOT/'docs/calibration-experiment'


def finite_quantile(scores, coverage=.8):
    values=np.sort(np.asarray(scores,dtype=float))
    if not len(values): raise ValueError('No interval calibration scores')
    rank=math.ceil((len(values)+1)*coverage)
    # Infinite interval if too few calibration units; never silently cap rank.
    return float(values[rank-1]) if rank<=len(values) else math.inf


def add_groups(rows):
    return rows.with_columns(
        lat_tile=(((pl.col('cell')//3600)*.1-90+.05)/10).floor().cast(pl.Int32),
        lon_tile=(((pl.col('cell')%3600)*.1-180+.05)/10).floor().cast(pl.Int32),
        quarter=((pl.col('month')-1)//3).cast(pl.Int32))


class RegionalRatio:
    KEYS={'global':[], 'season':['month'], 'tile':['lat_tile','lon_tile'],
          'tile_quarter':['lat_tile','lon_tile','quarter'], 'tile_month':['lat_tile','lon_tile','month']}
    def __init__(self,mode='global',prior=500.):
        self.mode,self.prior,self.global_ratio,self.tables=mode,prior,{},{}
    def fit(self,rows):
        d=add_groups(rows);keys=self.KEYS[self.mode]
        # Same year drift weighting as the existing calibration; no test weights.
        d=d.with_columns(w=pl.when(pl.col('year')>=2021).then(.5).otherwise(1.))
        for dn in ('d','n'):
            m,v=pl.col('m_'+dn),pl.col('y_'+dn)
            sm=float((d['m_'+dn]*d['w']).sum());sv=float((d['y_'+dn]*d['w']).sum())
            if sm<=0:raise ValueError('Training MODIS exposure must be positive')
            ratio=sv/sm;self.global_ratio[dn]=ratio
            if keys:
                self.tables[dn]=(d.group_by(keys).agg(sm=(m*pl.col('w')).sum(),sv=(v*pl.col('w')).sum())
                    .select(*keys,ratio=(pl.col('sv')+self.prior*ratio)/(pl.col('sm')+self.prior)))
        return self
    def predict(self,rows):
        d=add_groups(rows).with_row_index('_order');keys=self.KEYS[self.mode]
        total=np.zeros(rows.height,dtype=float)
        for dn in ('d','n'):
            if keys:
                t=d.join(self.tables[dn],on=keys,how='left').sort('_order')
                ratio=t['ratio'].fill_null(self.global_ratio[dn]).to_numpy()
            else:ratio=self.global_ratio[dn]
            total+=d['m_'+dn].to_numpy()*ratio
        return total
    def export(self):
        return dict(mode=self.mode,prior_modis_count=self.prior,global_ratio=self.global_ratio,
                    tables={dn:t.to_dicts() for dn,t in self.tables.items()})


def prepare(counts,years,availability,cells):
    months=availability.filter(pl.col('year').is_in(years)&pl.col('snpp_av')&pl.col('aqua_av')).select('year','month')
    if months.is_empty():raise ValueError('No supported S-NPP reference months')
    rows=(cells.join(months,how='cross').join(
        counts.filter(pl.col('year').is_in(years)).select('cell','year','month',
            m_d=pl.col('terra_d')+pl.col('aqua_d'),m_n=pl.col('terra_n')+pl.col('aqua_n'),
            y_d=pl.col('snpp_d'),y_n=pl.col('snpp_n')),on=['cell','year','month'],how='left')
        .with_columns(pl.col('m_d','m_n','y_d','y_n').fill_null(0))
        .with_columns(y=pl.col('y_d')+pl.col('y_n'),modis=pl.col('m_d')+pl.col('m_n')))
    return rows


def aggregate(rows,pred,weights,var=None):
    d=rows.with_columns(p=pl.Series(pred))
    if var is not None:d=d.with_columns(v=pl.Series(var))
    return (d.join(weights,on='cell').group_by('year','month').agg(
        reference=(pl.col('y')*pl.col('w')).sum(),prediction=(pl.col('p')*pl.col('w')).sum(),
        modis_raw=(pl.col('modis')*pl.col('w')).sum(),
        **({'variance':(pl.col('v')*pl.col('w')**2).sum()} if var is not None else {})).sort('year','month'))


def score(rows,pred,areas):
    scores={}
    for identifier,(_,weights,_) in areas.items():
        a=aggregate(rows,pred,weights)
        scores[identifier]=errors(a['reference'],a['prediction'])['wape_pct']
    grid=(rows.with_columns(p=pl.Series(pred)).group_by('region_cell','year','month')
          .agg(y=pl.col('y').sum(),p=pl.col('p').sum()))
    pooled=errors(grid['y'],grid['p'])['wape_pct']
    macro=float(np.mean([scores[k] for k in scores if k!='australia']))
    return dict(objective=float(np.mean([scores['australia'],pooled,macro])),
                area_wape=scores,pooled_half_degree_wape=pooled)


def fit_intervals(rows,pred,areas):
    x=np.asarray(pred,float)+1.
    # Nonnegative residual variance; +1 floor permits uncertainty when MODIS=0.
    design=np.column_stack([x,x*x]);scale=np.maximum(np.linalg.norm(design,axis=0),1.)
    coef=nnls(design/scale,(rows['y'].to_numpy()-pred)**2)[0]/scale
    phi,alpha=max(float(coef[0]),1.),float(coef[1])
    var=phi*x+alpha*x*x
    bands={}
    for identifier,(_,weights,_) in areas.items():
        a=aggregate(rows,pred,weights,var)
        scores=np.abs(a['prediction'].to_numpy()-a['reference'].to_numpy())/np.sqrt(a['variance'].to_numpy())
        bands[identifier]=dict(q80=finite_quantile(scores),n_calibration_months=len(scores))
    return dict(phi=phi,alpha=alpha,variance_floor_count=1.,area_bands=bands,
        scope='Residual reference agreement only. Monthly serial dependence prevents an exchangeability guarantee; fixed evaluation areas only, not arbitrary-AOI calibrated intervals.')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'results.json').exists():
        print('Experiment already completed. Read docs/calibration-experiment/results.json; no repeat test evaluation performed.')
        return
    groups=[set(TRAIN),set(SELECT),set(INTERVAL),set(TEST)]
    if any(a&b for i,a in enumerate(groups) for b in groups[i+1:]):raise ValueError('Experiment years overlap')
    specs=[('global',500.)]+[(mode,k) for mode in ('season','tile','tile_quarter','tile_month') for k in (500.,5000.)]
    protocol=dict(train_years=TRAIN,selection_years=SELECT,interval_years=INTERVAL,test_years=TEST,candidates=specs,
        selection='Mean of national WAPE, pooled 0.5-degree WAPE, and mean of three fixed geographic-sample WAPEs; candidate national WAPE must not exceed baseline by 2 percentage points.',
        reference='Native S-NPP only, months passing the existing pooled 30% availability check; no learned platform scaling or production predictions used.',
        audit='All complete overlap years were previously used in production refitting. 2024-2025 are retrospective holdouts for this experiment, not globally untouched project data. 2019-2021 were already inspected and are not used for selection or final scoring.',
        deployment='Offline candidate only. Never modifies production model, calendar, forecasts or UI.',
        region_boxes=[dict(id=i,name=n,bbox=b) for i,n,b in REGIONS],
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (OUT/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    print('Protocol saved. Counting existing QC-passed native records ...',flush=True)
    counts,_=h.count_detections();availability=h.sensor_availability(counts)
    cells=pl.read_parquet(ROOT/'data/processed/cells.parquet').select('country','cell','region_cell')
    areas={}
    for identifier,name,bbox in REGIONS:
        w,summary=cell_weights(AOI(type='bbox',bbox=bbox) if bbox else AOI(type='region',region='Australia'),cells)
        areas[identifier]=(name,w,summary['effective_cells'])
    train=prepare(counts,TRAIN,availability,cells)
    selection=prepare(counts,SELECT,availability,cells)
    models={};scores={}
    for mode,k in specs:
        name=mode if mode=='global' else '{}_{}'.format(mode,int(k))
        model=RegionalRatio(mode,k).fit(train);models[name]=model
        scores[name]=score(selection,model.predict(selection),areas)
        print('Selection',name,round(scores[name]['objective'],3),flush=True)
    baseline='global';national_limit=scores[baseline]['area_wape']['australia']+2.
    eligible=[k for k in models if scores[k]['area_wape']['australia']<=national_limit]
    selected=min(eligible,key=lambda key:scores[key]['objective'])
    frozen=dict(selected=selected,selection_scores=scores,national_guardrail_wape=national_limit,
                selected_model=models[selected].export(),baseline_model=models[baseline].export())
    frozen_text=json.dumps(frozen,indent=2,allow_nan=False)
    (OUT/'frozen-selection.json').write_text(frozen_text,encoding='utf-8')
    print('Selection frozen:',selected,'; fitting intervals on 2022 only.',flush=True)
    calibration=prepare(counts,INTERVAL,availability,cells)
    pred=models[selected].predict(calibration)
    intervals=fit_intervals(calibration,pred,areas)
    (OUT/'frozen-intervals.json').write_text(json.dumps(intervals,indent=2,allow_nan=False),encoding='utf-8')
    print('Model and intervals frozen. Evaluating 2024-2025 once ...',flush=True)
    test=prepare(counts,TEST,availability,cells)
    base_pred=models[baseline].predict(test);pred=models[selected].predict(test)
    v=intervals['phi']*(pred+1)+intervals['alpha']*(pred+1)**2
    original=json.loads((ROOT/'data/processed/harmonization_report.json').read_text())['variance_model']['full']
    old_v=original['phi']*base_pred+original['alpha']*base_pred**2
    result=[];monthly=[]
    for identifier,(name,weights,ncells) in areas.items():
        a=aggregate(test,pred,weights,v);b=aggregate(test,base_pred,weights,old_v)
        radius=intervals['area_bands'][identifier]['q80']*np.sqrt(a['variance'].to_numpy())
        k=_interp_log(max(ncells,1.),[(1,1.),(25,original.get('kappa_region',1.)),(1000,original.get('kappa_country',1.))])
        old_radius=Z80*k*np.sqrt(b['variance'].to_numpy())
        y,p=a['reference'].to_numpy(),a['prediction'].to_numpy()
        cuts=transitions(a.to_dicts())
        base_cuts=transitions(b.to_dicts())
        normalized=lambda values:100*float(np.mean(np.abs([c['excess_jump'] for c in values])))/float(y.mean())
        row=dict(id=identifier,name=name,n_months=a.height,baseline=errors(y,b['prediction']),candidate=errors(y,p),
            baseline_existing_interval_coverage_pct=100*float(np.mean(np.abs(y-b['prediction'].to_numpy())<=old_radius)),
            candidate_interval_coverage_pct=100*float(np.mean(np.abs(y-p)<=radius)),
            baseline_mean_interval_width=float((2*old_radius).mean()),candidate_mean_interval_width=float((2*radius).mean()),
            baseline_handoff_excess_pct=normalized(base_cuts),candidate_handoff_excess_pct=normalized(cuts))
        result.append(row)
        for i,m in enumerate(a.to_dicts()):
            monthly.append(dict(region=identifier,year=m['year'],month=m['month'],reference=m['reference'],
                baseline=float(b['prediction'][i]),candidate=m['prediction'],
                lo=max(0.,m['prediction']-radius[i]),hi=m['prediction']+radius[i]))
    available=test.select('year','month').unique().sort('year','month').to_dicts()
    output=dict(completed_at=datetime.now(timezone.utc).isoformat(),protocol=protocol,selected=selected,
        frozen_selection_sha256=hashlib.sha256(frozen_text.encode()).hexdigest(),intervals=intervals,
        evaluated_months=available,regions=result,production_changed=False,
        interpretation='Fresh out-of-fit evaluation for this experiment, not an untouched-project test. Reference-sensor agreement only. Interval diagnostics apply only to the four fixed geographic samples.')
    (OUT/'results.json').write_text(json.dumps(output,indent=2,allow_nan=False),encoding='utf-8')
    pl.DataFrame(monthly).write_csv(OUT/'monthly.csv')
    render(output,scores)
    print(json.dumps(result,indent=2));print('Production model unchanged.',flush=True)


def render(output,scores):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows=output['regions'];x=np.arange(len(rows));names=[r['name'].replace(' Australia sample','') for r in rows]
    fig,axes=plt.subplots(1,2,figsize=(12,5),layout='constrained')
    for offset,key,label,color in [(-.18,'baseline','National day/night','#ba713f'),(.18,'candidate','Regional candidate','#337d76')]:
        axes[0].bar(x+offset,[r[key]['wape_pct'] for r in rows],.36,label=label,color=color)
    axes[0].set(title='2024-2025 retrospective monthly error',ylabel='WAPE (%)');axes[0].legend()
    for offset,key,label,color in [(-.18,'baseline_existing_interval_coverage_pct','Existing interval','#ba713f'),(.18,'candidate_interval_coverage_pct','Candidate interval','#337d76')]:
        axes[1].bar(x+offset,[r[key] for r in rows],.36,label=label,color=color)
    axes[1].axhline(80,color='#192f38',ls='--');axes[1].set(title='Interval coverage on evaluation months',ylabel='Coverage (%)',ylim=(0,105));axes[1].legend()
    for a in axes:a.set_xticks(x,names,rotation=30,ha='right');a.grid(axis='y',alpha=.15)
    fig.suptitle('Offline experiment — selection: 2017; interval fitting: 2022\nNative S-NPP reference only; production unchanged',fontsize=13)
    fig.savefig(OUT/'comparison.png',dpi=160);plt.close(fig)
    lines=['# Regional calibration experiment','',
        '**Offline candidate; production unchanged.** No new external data, APIs, dependencies or downloads. Training parameters and reference-month selection were rebuilt from existing QC-passed native records.','',
        '## Protocol and limits','',output['protocol']['audit'],'',
        'Training: 2013, 2016, 2018, 2023. Selection: 2017. Residual interval fitting: 2022. Evaluation: available native S-NPP months in 2024-2025. Model choice and interval parameters were saved before evaluation. Historical, non-chronological splitting is retrospective transfer evaluation, not a prospective forecast.','',
        'The candidates use a national day/night ratio or ratios by calendar month, 10-degree geographic tile, tile/quarter, or tile/month. Sparse groups shrink toward the national ratio with 500 or 5000 MODIS-count prior strength. The selection objective balances national, pooled 0.5-degree and fixed-sample WAPE; national degradation is limited to 2 percentage points.','',
        'Selected model: **{}**. Evaluation months: {}. Geographic samples are rectangles, not biome masks or spatial holdouts.'.format(output['selected'],len(output['evaluated_months'])),'',
        '![Comparison](comparison.png)','',
        '| Sample | National-ratio WAPE | Candidate WAPE | Existing interval coverage | Candidate interval coverage |','|---|---:|---:|---:|---:|']
    for r in rows:lines.append('| {} | {:.1f}% | {:.1f}% | {:.1f}% | {:.1f}% |'.format(r['name'],r['baseline']['wape_pct'],r['candidate']['wape_pct'],r['baseline_existing_interval_coverage_pct'],r['candidate_interval_coverage_pct']))
    lines+=['','## Interpretation','',
        'Compare every region, interval width and handoff residual in results.json; improvement in a pooled selection score is not a deployment guarantee. The baseline ratios are rebuilt on experiment training years. Existing interval parameters were previously fit on 2017/2022, while candidate intervals use 2022 only, so uncertainty comparisons are not matched-budget experiments. Neither interval uses 2024/2025 outcomes.','',
        'Candidate variance is phi*(prediction+1)+alpha*(prediction+1)^2 with nonnegative coefficients, estimated from 2022 residuals. Each fixed area gets the finite-sample corrected 80% quantile of monthly standardized errors. Serial dependence and only one calibration year prevent a universal coverage guarantee. These area-specific intervals must not be presented as calibrated for arbitrary drawn polygons. Clipped lower bounds avoid negative counts. Satellite measurement/omission and scaling uncertainty are not included.','',
        'Months failing the pooled native S-NPP availability check are excluded, not filled with production-scaled NOAA values. The detection-count availability check does not establish cloud-free or daily viewing exposure. All skipped year/month combinations are identifiable from evaluated_months in results.json.','',
        'No Terra-only (2000-2002) model was changed. No automatic history updates were added. Production replacement needs further evidence and a compatible arbitrary-area uncertainty design. No complete year in this supplied archive can be called never-used project data; new prospective observations would be needed for that stronger claim.','',
        '## Files and reproducibility','',
        'protocol.json records the specification and script hash. frozen-selection.json records all selection scores and safe JSON model tables. frozen-intervals.json records uncertainty parameters saved before evaluation. monthly.csv provides evaluation values and intervals. results.json contains per-area errors, widths, coverage and handoff diagnostics.','',
        'Run `.venv/Scripts/python -m pipeline.calibration_experiment` from the project root. A completed run is not silently re-evaluated; its saved outputs are reused. No operational Parquet or model artifact is overwritten.']
    (OUT/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':main()

"""Training-only cross-validation with geographic safeguards; no deployment."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import polars as pl
from pipeline import harmonize as h
from pipeline.calibration_experiment import RegionalRatio, TRAIN, REGIONS, prepare, score, ROOT
from embercal_engine.aoi import cell_weights
from embercal_engine.schemas import AOI


def safeguards(candidate,baseline,tolerance_pp=1.,minimum_fold_wins=3):
    """National gains cannot hide regressions in a fixed geographic sample."""
    failures=[]
    if candidate['fold_wins']<minimum_fold_wins:
        failures.append('Fewer than three of four held-training-year objective wins')
    for area,value in candidate['mean_area_wape'].items():
        if value>baseline['mean_area_wape'][area]+tolerance_pp:
            failures.append('Regional error guardrail: '+area)
    if candidate['mean_objective']>=baseline['mean_objective']*.99:
        failures.append('Less than 1% relative mean-objective improvement')
    return dict(eligible=not failures,reasons=failures)


def main():
    out=ROOT/'docs/calibration-cross-validation';out.mkdir(parents=True,exist_ok=True)
    if (out/'results.json').exists():
        print('Training CV already completed; saved results reused.');return
    specs=[('global',500.)]+[(m,k) for m in ('season','tile','tile_quarter','tile_month') for k in (500.,5000.)]
    protocol=dict(years=TRAIN,candidates=specs,folds='Leave one training year out; retrospective, not chronological forecasting.',
        safeguard='At least 3 of 4 fold objective wins, >=1% relative mean-objective improvement, and no fixed-area mean WAPE degradation >1 percentage point.',
        evaluation_policy='No 2019-2021 or 2024-2025 evaluation metrics read or recomputed; no production replacement.',
        interval_policy='Existing candidate intervals apply only to four fixed areas; arbitrary-area validation is still required.')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    counts,_=h.count_detections();counts=counts.filter(pl.col('year').is_in(TRAIN))
    availability=h.sensor_availability(counts)
    cells=pl.read_parquet(ROOT/'data/processed/cells.parquet').select('country','cell','region_cell')
    allrows=prepare(counts,TRAIN,availability,cells)
    areas={}
    for identifier,name,bbox in REGIONS:
        weights,summary=cell_weights(AOI(type='bbox',bbox=bbox) if bbox else AOI(type='region',region='Australia'),cells)
        areas[identifier]=(name,weights,summary['effective_cells'])
    fold_results={}
    for year in TRAIN:
        train=allrows.filter(pl.col('year')!=year);validation=allrows.filter(pl.col('year')==year)
        fold={}
        for mode,k in specs:
            name=mode if mode=='global' else '{}_{}'.format(mode,int(k))
            model=RegionalRatio(mode,k).fit(train)
            fold[name]=score(validation,model.predict(validation),areas)
        fold_results[str(year)]=fold
        print('Completed held-training-year fold',year,flush=True)
    names=list(next(iter(fold_results.values())))
    summaries={}
    for name in names:
        summaries[name]=dict(mean_objective=float(np.mean([f[name]['objective'] for f in fold_results.values()])),
            fold_wins=sum(f[name]['objective']<f['global']['objective'] for f in fold_results.values()),
            mean_area_wape={area:float(np.mean([f[name]['area_wape'][area] for f in fold_results.values()])) for area in areas})
    baseline=summaries['global'];eligible=[]
    for name,s in summaries.items():
        s['gate']=safeguards(s,baseline)
        if s['gate']['eligible']:eligible.append(name)
    selected=min(eligible,key=lambda n:summaries[n]['mean_objective']) if eligible else 'global'
    payload=dict(protocol=protocol,folds=fold_results,summaries=summaries,recommended_for_further_evaluation=selected,
        production_replacement_approved=False,
        blocking_evidence=['No completely unused full evaluation year in supplied archive.','Arbitrary-area interval coverage has not been established.','Intended-user testing has not yet been conducted.'])
    (out/'results.json').write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
    lines=['# Calibration cross validation and geographic safeguards','',
        'Training-only evaluation. Production remains unchanged. Models are fitted four times, each time excluding one training year. Every fold re-estimates its global and group ratios. No previously inspected final-test outcomes are used for this selection.','',
        'Eligible corrections need at least three fold wins, at least 1% relative mean-objective improvement, and no geographic sample mean WAPE increase greater than one percentage point. The objective balances national, pooled half-degree, and fixed-sample errors.','',
        '| Candidate | Mean objective | Fold wins | Geographic gate |','|---|---:|---:|---|']
    for name,s in summaries.items():lines.append('| {} | {:.2f} | {}/4 | {} |'.format(name,s['mean_objective'],s['fold_wins'],'Eligible for further evaluation' if s['gate']['eligible'] else '; '.join(s['gate']['reasons'])))
    lines+=['','Recommended for further evaluation: **{}**.'.format(selected),'',
        'This recommendation is not production approval. Retrospective folds share geographic locations, omit observation exposure, and have only four independent year blocks. The safeguards protect the fixed samples, not every small polygon or biome. Do not change an already-tested candidate and reuse 2024-2025 as if those outcomes were still unseen.','',
        'The current national model remains the deployed model. Missing prospective evidence and arbitrary-area uncertainty calibration are explicit blockers. No automatic downloads or retraining were added. Run `.venv/Scripts/python -m pipeline.calibration_gate` to inspect or reproduce the saved gate workflow.']
    (out/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summaries,indent=2));print('Recommended for further evaluation:',selected,'; production approval: false')


if __name__=='__main__':main()

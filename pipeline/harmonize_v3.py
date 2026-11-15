"""Reference-scale harmonization with disjoint training, calibration and final test years.

The continuous record uses measured VIIRS reference values where available and
calibrated MODIS estimates otherwise. Overlapping sensors are never summed.
Cloud-free observation exposure cannot be inferred from a hotspot-only archive.
"""
from __future__ import annotations
import gc
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import numpy as np
import polars as pl
from pipeline import harmonize as h
from pipeline.common import PROCESSED, load_config

VERSION = 'harm-v3.1-calibrated-only'

def split_years(config):
    c=config['harmonization']; first,last=c['overlap_years']
    train=set(c['training_years']); calibration=set(c['calibration_years']); test=set(c['holdout_years'])
    if train & calibration or train & test or calibration & test:
        raise ValueError('Training, calibration and final test years must be disjoint')
    if any(not first <= y <= last for y in train | calibration | test):
        raise ValueError('Validation years must lie within the sensor overlap period')
    return sorted(train),sorted(calibration),sorted(test)

def rows_for(t, years, sampled=False):
    parts=[]
    hc=load_config()["harmonization"]
    weights=h.training_weight(hc["drift_downweight_from"],hc["drift_weight"])
    for country in t.country_codes:
        for i in range(0,len(years),h.YEARS_PER_CHUNK):
            r=h.build_rows(t,country,years[i:i+h.YEARS_PER_CHUNK])
            r=r.filter(pl.col('aqua_av') & pl.col('snpp_av') & (pl.col('coverage') >= .999))
            if sampled:
                r=r.filter(h.keep_for_training()).with_columns(w=weights)
            else:
                r=r.with_columns(w=pl.lit(1.,pl.Float32))
            parts.append(r)
    return pl.concat(parts)

def build_models(rows, k, terra=False):
    models={'stratified_eb': h.EmpiricalBayesRatio(k,terra).fit(rows),
            'gbm': h.GradientBoosting(h.FEATURES_TERRA_ONLY if terra else h.FEATURES_FULL).fit(rows)}
    if not terra: models['global_ratio']=h.GlobalRatio().fit(rows)
    return models

def variance(pred, parameters):
    return parameters['phi']*pred + parameters['alpha']*pred**2

def interval_test(rows,pred,parameters):
    d=rows.select('country','region_cell','year','month','y').with_columns(p=pl.Series(pred),v=pl.Series(variance(pred,parameters)))
    result={}
    for level,keys in [('region',['region_cell','year','month']),('country',['country','year','month'])]:
        a=d.group_by(keys).agg(pl.col('y').sum(),pl.col('p').sum(),pl.col('v').sum())
        radius=h.Z80*parameters[f'kappa_{level}']*np.sqrt(a['v'].to_numpy())
        result[level]=dict(n=a.height,coverage_80=float(np.mean(np.abs(a['y'].to_numpy()-a['p'].to_numpy())<=radius)))
    return result

def atomic_parquet(df,path):
    temp=path.with_name(path.name+'.part');df.write_parquet(temp,compression='zstd');temp.replace(path)

def main():
    cfg=load_config(); train_years,cal_years,test_years=split_years(cfg)
    PROCESSED.mkdir(parents=True,exist_ok=True)
    backup=PROCESSED.parent/'model_backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    outputs=['monthly_cells.parquet','monthly_meta.parquet','cells.parquet','harmonization_report.json','validation_cells.parquet']
    for name in outputs:
        if (PROCESSED/name).exists():
            backup.mkdir(parents=True,exist_ok=True);shutil.copy2(PROCESSED/name,backup/name)
    print('1/6 Counting the supplied Australian archives ...',flush=True)
    counts,last_date=h.count_detections()
    native=counts.select('country','cell','year','month',snpp=pl.col('snpp_d')+pl.col('snpp_n')).filter(pl.col('snpp')>0)
    atomic_parquet(native,PROCESSED/'native_snpp_monthly.parquet')
    # All learned climatology features and cross-platform ratios use training years only for evaluation.
    t=h.make_tables(counts,last_date,feature_years=train_years)
    print(f'2/6 Train={train_years}; calibration/model-selection={cal_years}; untouched final test={test_years}',flush=True)
    train=rows_for(t,train_years,True); calibration=rows_for(t,cal_years); test=rows_for(t,test_years)
    print(f'Rows: training {train.height:,}; calibration {calibration.height:,}; final test {test.height:,}',flush=True)
    models=build_models(train,cfg['harmonization']['shrinkage_k'])
    terra_models=build_models(train,cfg['harmonization']['shrinkage_k'],True)
    cal_metrics={name:h.evaluate(calibration,model.predict(calibration)) for name,model in models.items()}
    terra_cal_metrics={name:h.evaluate(calibration,model.predict(calibration)) for name,model in terra_models.items()}
    best=min(models,key=lambda n:h.selection_score(cal_metrics[n]));best_terra=min(terra_models,key=lambda n:h.selection_score(terra_cal_metrics[n]))
    uncertainty={
        'full':h.fit_uncertainty(calibration,models[best].predict(calibration)),
        'terra_only':h.fit_uncertainty(calibration,terra_models[best_terra].predict(calibration))}
    print(f'3/6 Chosen on calibration years: {best}; Terra-only: {best_terra}. Evaluating untouched test ...',flush=True)
    test_predictions={name:model.predict(test) for name,model in models.items()}
    metrics={name:h.evaluate(test,p) for name,p in test_predictions.items()}
    metrics['naive_modis']=h.evaluate(test,test['modis'].to_numpy().astype(float))
    terra_predictions={name:model.predict(test) for name,model in terra_models.items()}
    terra_metrics={name:h.evaluate(test,p) for name,p in terra_predictions.items()}
    p=test_predictions[best]; v=variance(p,uncertainty['full'])
    # Persist genuine out-of-sample predictions. Production refitting below cannot overwrite these.
    validation=test.select('country','cell','region_cell','year','month',reference=pl.col('y'),modis_raw=pl.col('modis')).with_columns(
        prediction=pl.Series(p,dtype=pl.Float32),variance=pl.Series(v,dtype=pl.Float32))
    atomic_parquet(validation,PROCESSED/'validation_cells.parquet')
    interval_coverage=interval_test(test,p,uncertainty['full'])
    del train,calibration,test,models,terra_models,test_predictions,terra_predictions,validation
    gc.collect()
    print('4/6 Refitting selected models on all available overlap years for the production record ...',flush=True)
    t=h.make_tables(counts,last_date)  # production can use every overlap year's MODIS features
    production=rows_for(t,list(range(cfg['harmonization']['overlap_years'][0],cfg['harmonization']['overlap_years'][1]+1)),True)
    def refit(name,terra=False):
        if name=='gbm': return h.GradientBoosting(h.FEATURES_TERRA_ONLY if terra else h.FEATURES_FULL).fit(production)
        if name=='global_ratio': return h.GlobalRatio().fit(production)
        return h.EmpiricalBayesRatio(cfg['harmonization']['shrinkage_k'],terra).fit(production)
    full=refit(best);terra=refit(best_terra,True)
    feature_importance=full.importance() if best=='gbm' else {}
    for name,model in [('full',full),('terra',terra)]:
        if isinstance(model,h.GradientBoosting): model.model.save_model(str(PROCESSED/f'harmonization_{name}_model.txt'))
    del production;gc.collect()
    print('5/6 Writing calibrated MODIS and separate observed reference ...',flush=True)
    stored=[];totals=[];years=sorted(t.months['year'].unique().to_list())
    for country in t.country_codes:
        for i in range(0,len(years),h.YEARS_PER_CHUNK):
            r=h.build_rows(t,country,years[i:i+h.YEARS_PER_CHUNK]); aqua=r['aqua_av'].to_numpy()
            pf,pt=full.predict(r),terra.predict(r)
            converted=np.where(aqua,pf,pt)
            model_var=np.where(aqua,variance(pf,uncertainty['full']),variance(pt,uncertainty['terra_only']))
            available=r['coverage'].to_numpy()>0
            r=r.with_columns(
                modis_harmonized=pl.Series(np.where(available,converted,np.nan),dtype=pl.Float32).fill_nan(None),
                modis_model_var=pl.Series(np.where(available,model_var,np.nan),dtype=pl.Float32).fill_nan(None),
                viirs_ref=pl.col('y'),snpp=pl.col('snpp_d')+pl.col('snpp_n'),n20=pl.col('n20_d')+pl.col('n20_n'),n21=pl.col('n21_d')+pl.col('n21_n'))
            totals.append(r.filter(pl.col('coverage')>0).group_by('country','year','month').agg(pl.col('modis').sum(),pl.col('modis_harmonized').sum(),y=pl.col('y').fill_null(0).sum()))
            # Keep predictions even when observed VIIRS is zero: they are essential for assessing the model.
            keep=(pl.col('modis_harmonized')>=h.MIN_STORED_VALUE)|(pl.col('modis')>0)|(pl.col('viirs_ref').fill_null(0)>0)
            stored.append(r.filter(pl.col('modis_harmonized').is_not_null()&keep).select('country','cell','region_cell','year','month','terra','aqua','modis','snpp','n20','n21','viirs_ref','modis_harmonized','modis_model_var','modis_frp','snpp_frp'))
            print(f'  {country} {years[i]}–{years[min(i+1,len(years)-1)]}',flush=True)
    monthly=pl.concat(stored);atomic_parquet(monthly,PROCESSED/'monthly_cells.parquet');atomic_parquet(t.cells,PROCESSED/'cells.parquet')
    meta=(pl.DataFrame({'country':list(t.country_codes)}).join(t.months,how='cross').join(t.availability,on=['year','month'],how='left').select('country','year','month','coverage',
        method=pl.when(pl.col('coverage')<=0).then(pl.lit('no_data')).when(pl.col('snpp_av')).then(pl.lit('viirs_observed')).when(pl.col('n20_av')|pl.col('n21_av')).then(pl.lit('viirs_scaled')).when(pl.col('aqua_av')).then(pl.lit('modis_model')).otherwise(pl.lit('modis_model_terra_only')),
        viirs_src=pl.when(pl.col('snpp_av')).then(pl.lit('S-NPP')).when(pl.col('n20_av')).then(pl.lit('NOAA-20 (scaled)')).when(pl.col('n21_av')).then(pl.lit('NOAA-21 (scaled)')).otherwise(None)))
    atomic_parquet(meta,PROCESSED/'monthly_meta.parquet')
    report=dict(method_version=VERSION,unit='VIIRS S-NPP-equivalent hotspots',countries=list(t.country_codes),grid=cfg['grid'],cleaning=cfg['cleaning'],
        overlap_years=cfg['harmonization']['overlap_years'],training_years=train_years,calibration_years=cal_years,holdout_years=test_years,
        selected_model=best,selected_model_terra_only=best_terra,selection_metrics=cal_metrics,selection_metrics_terra_only=terra_cal_metrics,
        holdout_metrics=metrics,holdout_metrics_terra_only=terra_metrics,variance_model=uncertainty,test_interval_coverage=interval_coverage,
        gbm_feature_importance=feature_importance,features=h.FEATURES_FULL,terra_features=h.FEATURES_TERRA_ONLY,
        noaa20_to_snpp_ratio=t.ratios,step_change_tests=h.sensor_switch_test(pl.concat(totals)),rows_stored=monthly.height,data_last_date=str(last_date),
        evaluation_protocol='Training-only climatology and platform scaling; calibration years choose the model and fit residual intervals; disjoint final test years assess performance. Production model refitted after frozen test predictions.',
        output_series='modis_harmonized',
        reference_policy='S-NPP and scaled NOAA observations supply the training and comparison reference only. The historical activity series is calibrated MODIS for every available month; there is no sensor-switching output.',
        uncertainty_scope='Residual calibration uncertainty for MODIS estimates only; satellite measurement/omission uncertainty is unknown. No zero-width confidence interval is assigned to measured or scaled reference values.',
        coverage_scope='Temporal span of supplied partitions, not cloud-free viewing exposure. A zero is no recorded quality-filtered detections, not evidence of no fire.',
        limitations=['Sensor-reference agreement is not independent ground truth.','Observation opportunities, clouds and missed fires are not recovered from hotspot-only files.','Retrospective transfer to 2000–2011 and orbit drift remain sources of uncertainty.'],
        built_at=datetime.now(timezone.utc).isoformat())
    temp=PROCESSED/'harmonization_report.json.part';temp.write_text(json.dumps(report,indent=2,default=float),encoding='utf-8');temp.replace(PROCESSED/'harmonization_report.json')
    print(f'6/6 Complete: {monthly.height:,} cell-month rows. Independent test metrics: {metrics[best]}',flush=True)

if __name__=='__main__': main()

"""Aggregate frozen out-of-sample predictions for the user's actual AOI."""
import numpy as np
import polars as pl

def evidence_for(store,weights,effective_cells):
    path=store.dir/'validation_cells.parquet'
    if not path.exists():
        return {'available':False,'reason':'Independent test predictions are unavailable. Rebuild harmonization with the v3 pipeline.'}
    d=(pl.scan_parquet(path).join(weights.lazy(),on='cell',how='inner').group_by('year','month').agg(
        reference=(pl.col('reference')*pl.col('w')).sum(),
        prediction=(pl.col('prediction')*pl.col('w')).sum(),
        modis_raw=(pl.col('modis_raw')*pl.col('w')).sum(),
        variance=(pl.col('variance')*pl.col('w')**2).sum()).collect().sort('year','month'))
    if d.is_empty(): return {'available':False,'reason':'No final-test cells overlap this selection.'}
    y,p=d['reference'].to_numpy(),d['prediction'].to_numpy()
    total=float(y.sum())
    def metrics(pred):
        return {'monthly_mae':float(np.abs(pred-y).mean()),'wape_pct':float(np.abs(pred-y).sum()/total*100) if total else None,
                'bias_pct':float((pred-y).sum()/total*100) if total else None}
    params=store.harm_report['variance_model']['full']
    from .analytics import _interp_log,Z80
    k=_interp_log(max(effective_cells,1.),[(1,1.),(25,params.get('kappa_region',1.)),(1000,params.get('kappa_country',1.))])
    radius=Z80*k*np.sqrt(d['variance'].to_numpy())
    monthly=[{'year':int(r['year']),'month':int(r['month']),'reference':float(r['reference']),'prediction':float(r['prediction']),
        'modis_raw':float(r['modis_raw']),'lo':float(max(0,r['prediction']-radius[i])),'hi':float(r['prediction']+radius[i])} for i,r in enumerate(d.iter_rows(named=True))]
    return {'available':True,'evaluation_type':'independent_final_test','years':sorted(d['year'].unique().to_list()),
        'months':d.height,'model':metrics(p),'raw_modis':metrics(d['modis_raw'].to_numpy()),
        'interval_coverage_80':float(np.mean(np.abs(p-y)<=radius)), 'monthly':monthly,
        'note':'These predictions were frozen before production refitting. Test years were excluded from model selection, interval fitting, learned climatology and platform scaling. Agreement is with a reference sensor, not independent ground truth.'}

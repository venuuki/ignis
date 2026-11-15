import test from 'node:test';
import assert from 'node:assert/strict';
import {seasonView,anomalyView} from '../web/src/activitySummaryModel.mjs';

test('anomaly distinguishes percent of median from percentile and preserves zero',()=>{
  const a=anomalyView({count:200,median:100,percentile:75,baseline_n_years:20,partial:false});
  assert.equal(a.ratio,2);assert.equal(a.label,'Higher than usual');assert.equal(a.percentile,75);
  assert.equal(anomalyView({count:0,median:10,percentile:0,baseline_n_years:20}).label,'Unusually low');
});
test('partial months and short baselines never receive a confident visual category',()=>{
  assert.equal(anomalyView({count:200,median:100,percentile:95,baseline_n_years:20,partial:true}).supported,false);
  assert.equal(anomalyView({count:200,median:100,percentile:95,baseline_n_years:2}).label,'Limited baseline');
  assert.equal(anomalyView({count:0,median:0,percentile:50,baseline_n_years:20}).ratio,null);
  assert.equal(anomalyView(null).percentile,null);
});
test('season handles year-round and cross-year windows without inventing missing baseline zeros',()=>{
  const climatology=Array.from({length:12},(_,i)=>({month:i+1,median:i+1,n_years:20}));
  assert.equal(seasonView({climatology,season:{has_season:true,peak_month:12,season_months:[11,12,1,2]}}).title,'November – February');
  assert.equal(seasonView({climatology,season:{has_season:true,peak_month:12,season_months:climatology.map(m=>m.month)}}).title,'Activity throughout the year');
  const empty=seasonView({climatology:[]});assert.equal(empty.title,'Baseline unavailable');assert.equal(empty.months[0].value,null);
});
test('season falls back to means only when all medians are zero',()=>{
  const s=seasonView({climatology:[{month:1,median:0,mean:5,n_years:20}],season:{has_season:true,peak_month:1,season_months:[1]}});
  assert.equal(s.basis,'mean');assert.equal(s.months[0].value,5);
});

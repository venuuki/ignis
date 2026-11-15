type Data=Record<string,any>;
const fmt=(n:any,d=1)=>n==null?'—':Number(n).toLocaleString('en-AU',{maximumFractionDigits:d});
export default function EvidenceSummary({evidence}:{evidence?:Data}) {
  if(!evidence?.available) return <p className="notice">{evidence?.reason||'Independent test results are loading.'}</p>;
  return <><p>{evidence.note}</p><div className="ec-stats">
    <div className="ec-stat"><span>Test monthly WAPE · model</span><strong>{fmt(evidence.model.wape_pct)}%</strong><p>Raw MODIS: {fmt(evidence.raw_modis.wape_pct)}% · lower disagreement is better</p></div>
    <div className="ec-stat"><span>Signed model bias</span><strong>{fmt(evidence.model.bias_pct)}%</strong><p>Positive means overestimation against the reference</p></div>
    <div className="ec-stat"><span>Test coverage of nominal 80% range</span><strong>{fmt(evidence.interval_coverage_80*100)}%</strong><p>{evidence.months} test months · {evidence.years.join(', ')}</p></div>
  </div><p className="caption">WAPE is summed absolute monthly disagreement divided by reference total. Scores describe this area and reference; they are not probabilities of real fire detection. Test coverage can differ from 80%.</p></>;
}

import {useEffect,useRef,useState,type CSSProperties} from 'react';
import {ArrowDownRight,ArrowUpRight,CalendarDays,Clock3,Download,Flame,Info,MapPin,Minus,RefreshCw} from 'lucide-react';
import {toPng} from 'html-to-image';
import {anomalyView,arcPath,MONTH_NAMES,seasonView} from './activitySummaryModel.mjs';
import './activitySummary.css';
import HelpHint from './HelpHint';

type Data=Record<string,any>;
const fmt=(v:any)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-AU',{maximumFractionDigits:v>0&&v<1?1:0}):'—';
const bands=[{from:0,to:10,color:'#548199'},{from:10,to:33.3,color:'#91b7c6'},{from:33.3,to:66.7,color:'#b5ceca'},{from:66.7,to:90,color:'#d7b18a'},{from:90,to:100,color:'#b86d41'}];

export default function ActivitySummary({aoi,areaName,baselineStart,baselineEnd,series,latestComplete,enabled,pending}: {aoi:Data;areaName:string;baselineStart:number;baselineEnd:number;series:string;latestComplete?:string;enabled:boolean;pending:boolean}) {
  const [data,setData]=useState<Data|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [retry,setRetry]=useState(0),[selected,setSelected]=useState<number|null>(null),[saving,setSaving]=useState(false),[saveError,setSaveError]=useState('');
  const capture=useRef<HTMLElement>(null),aoiKey=JSON.stringify(aoi);
  useEffect(()=>{
    setData(null);setError('');setSelected(null);setSaveError('');setBusy(false);
    if(!enabled||pending||!latestComplete)return;
    const c=new AbortController();setBusy(true);
    const timer=setTimeout(async()=>{
      try{
        const response=await fetch('/api/analysis/calendar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({aoi:JSON.parse(aoiKey),start:latestComplete,end:latestComplete,baseline_start:baselineStart,baseline_end:baselineEnd,series}),signal:c.signal});
        const body=await response.json();
        if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'Choose a valid area and baseline to see the activity summary.');
        if(!c.signal.aborted)setData(body);
      }catch(e){if(!c.signal.aborted)setError((e as Error).message);}
      finally{if(!c.signal.aborted)setBusy(false);}
    },550);
    return()=>{clearTimeout(timer);c.abort();};
  },[aoiKey,baselineStart,baselineEnd,series,latestComplete,enabled,pending,retry]);
  const seasonal=seasonView(data),latest=data?.current?.latest_complete,anomaly=anomalyView(latest);
  const month=selected||seasonal.peak||1,detail=seasonal.months[month-1];
  const partial=data?.current?.latest?.partial?data.current.latest:null;
  const Direction=anomaly.direction>0?ArrowUpRight:anomaly.direction<0?ArrowDownRight:Minus;
  async function save(){
    if(!capture.current)return;
    setSaving(true);setSaveError('');
    try{
      const image=await toPng(capture.current,{backgroundColor:'#ffffff',pixelRatio:2,filter:node=>!(node instanceof HTMLElement&&node.hasAttribute('data-summary-control'))});
      const link=document.createElement('a');link.href=image;link.download='embercal-area-activity-summary.png';link.click();
    }catch{setSaveError('The summary image could not be saved. Try again after the page finishes loading.');}
    finally{setSaving(false);}
  }
  return <section className="activity-summary" ref={capture} aria-labelledby="activity-summary-title" aria-busy={busy}>
    <header className="activity-summary-header"><div><h2 id="activity-summary-title">Fire activity at a glance</h2><span className="summary-location"><MapPin size={13}/>{areaName}<span>Baseline {baselineStart}–{baselineEnd}</span></span></div><button className="button" onClick={save} disabled={!data||saving||busy} data-summary-control><Download size={14}/>{saving?'Saving…':'Save summary'}</button></header>
    {busy?<div className="activity-summary-loading" role="status"><RefreshCw size={22} className="summary-spin"/><span>Reading this area’s fire history…</span><div/><div/></div>
    :error?<div role="alert" className="summary-unavailable"><Info size={24}/><h3>Summary unavailable</h3><p>{error}</p><button className="button" onClick={()=>setRetry(n=>n+1)} data-summary-control>Try again</button></div>
    :!data?<div className="summary-unavailable"><CalendarDays size={26}/><h3>{pending?'Draw your area to reveal its seasonal pattern':'Historical summary is not available yet'}</h3><span>{pending?'The map and summary use the same selection.':'The recent live map can still be used independently.'}</span></div>
    :<>
      <div className="activity-summary-grid">
        <article className="season-visual"><div className="summary-card-top"><span><CalendarDays size={17}/>USUAL ACTIVE SEASON<HelpHint label="About the active season">The active window follows months with typical activity at least 20% of the peak. Select a bar to inspect a month.</HelpHint></span>{seasonal.limited&&<span className="summary-muted-tag">Limited history</span>}</div><h3>{seasonal.title}</h3>
          <div className="season-peak">{seasonal.peak?<><Flame size={16}/><strong>{MONTH_NAMES[seasonal.peak-1]}</strong><span>usual peak</span></>:<><Minus size={16}/><span>No clear peak in the available baseline</span></>}</div>
          <div className="season-strip" role="group" aria-label="Typical activity by calendar month">
            {seasonal.months.map((m:any,i:number)=>{const active=seasonal.active.includes(m.month),peak=m.month===seasonal.peak,chosen=m.month===month;return <button key={m.month} className={`season-month ${peak?'season-month-peak':active?'season-month-active':''} ${m.value===null?'season-month-missing':''}`} aria-pressed={chosen} aria-label={`${m.name}: ${m.value===null?'baseline unavailable':fmt(m.value)+' typical detections'}${peak?', usual peak':''}${active?', within usual active season':''}`} title={`${m.name} · ${fmt(m.value)} ${seasonal.basis} detections · ${m.years} baseline years`} onClick={()=>setSelected(m.month)}><span className="season-bar-well"><span className="season-bar" style={{'--bar-height':`${m.value===null?0:Math.max(3,seasonal.max>0?m.value/seasonal.max*100:0)}%`,'--bar-delay':`${i*25}ms`} as CSSProperties}/>{peak&&<Flame size={12} className="season-peak-mark"/>}</span><span className="season-month-label">{m.name.slice(0,3)}</span><span className="season-selection-dot"/></button>;})}
          </div>
          <div className="season-legend"><span><i/>Lower activity</span><span><i/>Active window</span><span><i/>Peak month</span></div>
          <div className="season-inspector" aria-live="polite"><strong>{detail.name}</strong><span><b>{fmt(detail.value)}</b> typical detections</span><small>{detail.years} baseline years · {seasonal.basis}</small></div>
          
        </article>
        <article className={`anomaly-visual anomaly-${anomaly.tone}`}><div className="summary-card-top"><span><Direction size={18}/>LATEST COMPLETE MONTH<HelpHint label="About the monthly comparison">The gauge compares this month with the same calendar month in the baseline. The usual count is the historical median. Hotspots are satellite detections, not individual fires.</HelpHint></span></div><h3>{latest?`${MONTH_NAMES[latest.month-1]} ${latest.year}`:latestComplete||'Unavailable'}</h3>
          <div className="anomaly-gauge" role="img" aria-label={anomaly.supported?`${anomaly.label}. Percentile ${anomaly.percentile} compared with ${anomaly.years} baseline months.`:anomaly.label}>
            <svg viewBox="0 0 240 156" aria-hidden="true"><path d={arcPath(0,100)} fill="none" stroke="#edf1f0" strokeWidth="16"/>{anomaly.supported&&bands.map(b=><path key={b.from} d={arcPath(b.from+.6,b.to-.6)} fill="none" stroke={b.color} strokeWidth="16" strokeLinecap="butt"/>)}{anomaly.supported&&<g className="anomaly-indicator" style={{transform:`rotate(${(anomaly.percentile??0)*1.8}deg)`,transformOrigin:'120px 120px'}}><line x1="25" y1="120" x2="52" y2="120" stroke="#203b40" strokeWidth="2.5"/><circle cx="37" cy="120" r="5" fill="#203b40" stroke="white" strokeWidth="2"/></g>}<text x="28" y="150" textAnchor="middle">Lower</text><text x="120" y="150" textAnchor="middle">Usual</text><text x="212" y="150" textAnchor="middle">Higher</text></svg>
            <div className="anomaly-gauge-center"><Direction size={27}/><strong>{anomaly.label}</strong>{anomaly.ratio!==null?<span><b>{anomaly.ratio.toLocaleString('en-AU',{maximumFractionDigits:1})}×</b> the usual count</span>:<span>{anomaly.available&&latest?.median===0?'Typical median is zero':'More baseline history is needed'}</span>}</div>
          </div>
          <div className="anomaly-counts"><div><span>Recorded activity</span><strong>{fmt(latest?.count)}</strong></div><div><span>Typical {latest?MONTH_NAMES[latest.month-1].slice(0,3):'month'}</span><strong>{fmt(latest?.median)}</strong></div></div>
          <div className="anomaly-context">{anomaly.supported?<><strong>{Math.round(anomaly.percentile??0)}<small>/100</small></strong><span>historical percentile<br/>{anomaly.years} baseline {latest?MONTH_NAMES[latest.month-1]+'s':'months'}</span></>:<span>{anomaly.years} baseline years · At least 5 needed for a visual classification</span>}</div>
        </article>
      </div>
      <footer className="summary-bottom"><span>{data.series_info?.label}</span>{partial?<span className="summary-partial"><Clock3 size={14}/>{partial.name} is incomplete · excluded from comparison</span>:null}</footer>
    </>}
    {saveError&&<p role="alert" className="notice warning" data-summary-control>{saveError}</p>}
  </section>;
}

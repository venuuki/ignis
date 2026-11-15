import React, { useEffect, useState, useMemo, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { Flame, ArrowUpRight, ArrowRight, Globe2, Download, Info, MapPin, TriangleAlert } from 'lucide-react';
import { get, params, type Row } from './api';
import { REGIONS, csv } from './analytics.mjs';
import './styles/tokens.css';
import './style.css';
import './revamp.css';
import Analysis from './Analysis';
const AustraliaMap = React.lazy(() => import('./Map'));
const fmt = (v: any, decimals = 0) => v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: decimals });
const nav = [['analysis','Australia analysis',Globe2], ['observations','Observations',Flame]] as const;
function download(name: string, rows: Row[]) {
  const url = URL.createObjectURL(new Blob([csv(rows)], {type:'text/csv;charset=utf-8'}));
  const a = document.createElement('a'); a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function useData(path: string) {
  const [state,setState] = useState<{data:any;error:string;loading:boolean}>({data:null,error:'',loading:true});
  const [version,setVersion]=useState(0);
  useEffect(()=>{
    const controller=new AbortController();
    setState({data:null,error:'',loading:true});
    get(path,controller.signal).then(data=>setState({data,error:'',loading:false})).catch(e=>{
      if(!controller.signal.aborted) setState({data:null,error:e.message,loading:false});
    });
    return ()=>controller.abort();
  },[path,version]);
  return {...state, reload:()=>setVersion(v=>v+1)};
}
function Status({state}: {state:ReturnType<typeof useData>}) {
  if(state.loading) return <div role="status" className="empty-state">Loading backend results…</div>;
  if(state.error) return <div role="alert" className="notice warning"><TriangleAlert size={18}/><div><strong>Data could not be loaded</strong><p>{state.error}</p><button className="button" onClick={state.reload}>Retry</button></div></div>;
  return null;
}
function Notice({children,warning=false}: {children:React.ReactNode;warning?:boolean}) {
  return <div className={'notice '+(warning?'warning':'')}><Info size={17}/><div>{children}</div></div>;
}
function Stats({items}: {items: [string,React.ReactNode,string][]}) {
  return <div className="ec-stats">{items.map(([label,value,note])=><div className="ec-stat" key={label}><span>{label}</span><strong>{value}</strong><p>{note}</p></div>)}</div>;
}
function DataTable({rows,columns}: {rows:Row[];columns?:string[]}) {
  if(!rows.length) return <div className="empty-state">No returned records. Observation coverage remains unknown.</div>;
  const keys=columns||Object.keys(rows[0]);
  return <div className="table-scroll"><table><thead><tr>{keys.map(k=><th key={k}>{k.replaceAll('_',' ')}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr key={i}>{keys.map(k=><td key={k}>{typeof r[k]==='number'?fmt(r[k],3):String(r[k]??'—')}</td>)}</tr>)}</tbody></table></div>;
}
function Observations(){
  const [sensor,setSensor]=useState('all'),[confidence,setConfidence]=useState('all'),[day,setDay]=useState(''),[grid,setGrid]=useState('10'),[page,setPage]=useState(0);
  const state=useData('observations?'+params({sensor,confidence,...(day?{day}:{})}));
  const aligned=useData('alignment?'+params({grid_km:grid,confidence}));
  useEffect(()=>setPage(0),[sensor,confidence,day]);
  const rows:Row[]=state.data?.rows||[];
  const points=useMemo(()=>rows.map(r=>({...r,lat:r.latitude,lon:r.longitude,date:r.date_utc,time:r.acquisition_datetime_utc,sensor_confidence:r.confidence_raw})),[state.data]);
  return <><Notice>Saved NASA FIRMS observations · 8–12 November 2026 UTC · southeastern Australia. This is a saved snapshot, not a live feed. Recent NRT records include all returned hotspot types.</Notice>
    <div className="ec-toolbar"><label>Sensor<select aria-label="Observation sensor" value={sensor} onChange={e=>setSensor(e.target.value)}><option value="all">Both sensors</option><option value="modis">MODIS</option><option value="viirs">VIIRS</option></select></label><label>VIIRS confidence<select value={confidence} onChange={e=>setConfidence(e.target.value)}><option value="all">All confidence levels</option><option value="nominal_high">Nominal + high (MODIS unchanged)</option></select></label><label>UTC day<select value={day} onChange={e=>setDay(e.target.value)}><option value="">All five days</option>{[8,9,10,11,12].map(d=><option key={d}>2026-11-{String(d).padStart(2,'0')}</option>)}</select></label><button className="button" disabled={!rows.length} onClick={()=>download('embercal-raw-observations.csv',rows.map(r=>({...r,coverage_status:'unknown',dataset_scope:'saved FIRMS NRT; all returned types'})))}><Download size={15}/> Export rows</button></div>
    <Status state={state}/>{state.data&&<><Stats items={[["MODIS records",fmt(state.data.modis),'Current sensor, confidence and day filters'],["VIIRS records",fmt(state.data.viirs),'Current sensor, confidence and day filters'],["Observation coverage",'Unknown','No detected hotspot does not mean no fire']]}/><Suspense fallback={<div className="empty-state">Loading map…</div>}><AustraliaMap region={REGIONS[0]} records={points} observed allowDrawing={false} onArea={()=>{}}/></Suspense><section className="ec-card"><h2>Raw observation records</h2><p>Native sensor confidence is retained. It is not a probability of a real fire.</p><DataTable rows={rows.slice(page*50,(page+1)*50)} columns={['sensor','satellite','date_utc','acquisition_datetime_utc','latitude','longitude','confidence_raw','frp','daynight']}/><div className="pagination"><button className="button" disabled={page===0} onClick={()=>setPage(p=>p-1)}>Previous</button><span>Page {page+1} of {Math.max(1,Math.ceil(rows.length/50))} · {rows.length} records</span><button className="button" disabled={(page+1)*50>=rows.length} onClick={()=>setPage(p=>p+1)}>Next</button></div></section></>}
    <section className="ec-card"><div className="ec-card-heading"><div><h2>Spatial alignment</h2><p>Original EPSG:3577 grid calculation · both sensors · all five days. The confidence filter and grid size apply here; sensor/day map filters do not.</p></div><label>Grid size<select value={grid} onChange={e=>setGrid(e.target.value)}>{[5,10,20].map(k=><option key={k} value={k}>{k} km</option>)}</select></label></div><Status state={aligned}/>{aligned.data&&<><p><strong>{fmt(aligned.data.cells)}</strong> distinct grid cells in the alignment output.</p><DataTable rows={Object.entries(aligned.data.statuses).map(([status,count])=>({detection_status:status,cell_day_rows:count}))}/><small>Statuses count cell-day rows. “Neither” is not evidence of an absence of fire; coverage is unknown.</small></>}</section>
  </>;
}
function App(){
  const initial=()=>{const p=location.hash.slice(1);return nav.some(([id])=>id===p)?p:'analysis';};
  const [page,setPage]=useState(initial);const status=useData('status');
  useEffect(()=>{const handler=()=>setPage(initial());addEventListener('hashchange',handler);return()=>removeEventListener('hashchange',handler);},[]);
  function go(id:string){location.hash=id;setPage(id);window.scrollTo(0,0);}
  const titles:Record<string,[string,string]>={analysis:['Australia, across fire seasons.','Harmonizing MODIS and VIIRS · NASA Space Apps 2026 prototype.'],observations:['The observations behind the numbers.','Inspect saved detections, native confidence and spatial alignment.']};
  return <><a className="skip-link" href="#main">Skip to content</a><header className="topbar"><button className="brand" onClick={()=>go('analysis')} aria-label="Embercal home"><span className="brand-icon"><Flame size={27} strokeWidth={1.6}/></span>Ember<span>cal</span><span className="beta">RESEARCH</span></button><div className="top-right"><span className="data-pill"><span/>{status.loading?'Checking backend…':status.error?'Backend unavailable':status.data?.configured?'FIRMS key configured':'Saved data · FIRMS key not configured'}</span><a className="button small" href="/docs" target="_blank" rel="noreferrer">API documentation <ArrowUpRight size={14}/></a></div></header><div className="ec-app"><aside className="ec-navigation"><span className="eyebrow">THE OBSERVATORY</span><nav aria-label="Main navigation">{nav.map(([id,label,Icon])=><button key={id} className={page===id?'active':''} aria-current={page===id?'page':undefined} onClick={()=>go(id)}><Icon size={18}/>{label}{page===id&&<ArrowRight size={14}/>}</button>)}</nav><div className="nav-foot"><MapPin size={16}/><strong>Australia is our prototype.</strong><p>National fire history and local area selection.</p><span>NASA FIRMS · OpenStreetMap · Natural Earth</span></div></aside><main id="main" className="ec-main" tabIndex={-1}><div className="page-intro"><div><div className="eyebrow">SATELLITE DATA. A CLEARER PICTURE.</div><h1>{titles[page][0]}</h1><p>{titles[page][1]}</p></div></div><div className="ec-content">{page==='observations'?<Observations/>:<Analysis/>}</div><footer className="ec-footer"><span><Flame size={14}/> Embercal · Research prototype</span><span>Hotspots ≠ individual fires · Experimental outlooks · Observation coverage unknown</span></footer></main></div></>;
}
createRoot(document.getElementById('root')!).render(<App/>);

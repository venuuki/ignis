import {useCallback, useEffect, useRef, useState} from 'react';
import {Download, RefreshCw} from 'lucide-react';
import HelpHint from './HelpHint';

type Data=Record<string,any>;
const stamp=(value?:string)=>value ? new Date(value).toLocaleString('en-AU',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'UTC',hour12:false})+' UTC' : 'Not available';
const count=(n?:number)=>n==null?'—':n.toLocaleString('en-AU');

export function useLiveHotspots(aoi:Data, enabled:boolean) {
  const [hours,setHours]=useState(24),[quality,setQuality]=useState(true);
  const [data,setData]=useState<Data|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [revision,setRevision]=useState(0),[exportError,setExportError]=useState('');
  const aoiKey=JSON.stringify(aoi);
  const request=useRef<AbortController|null>(null);
  useEffect(()=>{
    setData(null);setError('');setExportError('');setBusy(false);
    if(!enabled)return;
    let active=true;
    const load=async()=>{
      request.current?.abort();const c=new AbortController();request.current=c;
      setBusy(true);setError('');
      try {
        const r=await fetch('/api/analysis/live/hotspots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({aoi:JSON.parse(aoiKey),hours,quality_only:quality}),signal:c.signal});
        const value=await r.json();
        if(!r.ok)throw new Error(typeof value.detail==='string'?value.detail:'The recent feed could not be loaded for this selection.');
        if(active&&!c.signal.aborted)setData(value);
      }catch(e){if(active&&!c.signal.aborted)setError((e as Error).message);}
      finally{if(active&&!c.signal.aborted)setBusy(false);}
    };
    const first=setTimeout(load,450);
    const timer=setInterval(()=>{if(document.visibilityState==='visible')void load();},300000);
    const visible=()=>{if(document.visibilityState==='visible')void load();};
    document.addEventListener('visibilitychange',visible);
    return()=>{active=false;clearTimeout(first);clearInterval(timer);document.removeEventListener('visibilitychange',visible);request.current?.abort();};
  },[aoiKey,hours,quality,enabled,revision]);
  const refresh=useCallback(()=>setRevision(r=>r+1),[]);
  const exportCsv=async()=>{
    setExportError('');
    try{
      const r=await fetch('/api/analysis/live/export.csv',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({aoi,hours,quality_only:quality})});
      if(!r.ok){const d=await r.json();throw new Error(typeof d.detail==='string'?d.detail:'Recent hotspot export failed.');}
      const url=URL.createObjectURL(await r.blob());const link=document.createElement('a');link.href=url;link.download='embercal-recent-hotspots.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){setExportError((e as Error).message);}
  };
  return {data,error,busy,hours,setHours,quality,setQuality,refresh,exportCsv,exportError};
}

export default function LiveHotspots({feed,mode,onMode,drawPending}:{feed:ReturnType<typeof useLiveHotspots>;mode:string;onMode:(mode:'live'|'archive')=>void;drawPending:boolean}) {
  const {data,error,busy}=feed;
  return <section className="live-feed" aria-label="Recent hotspot feed">
    <div className="live-heading"><div><h2>Recent hotspots</h2><HelpHint label="About the live feed">NASA FIRMS near-real-time detections. Today is incomplete and publication delays apply. Multiple satellites can observe the same fire. The feed refreshes every five minutes.</HelpHint></div><div className="ec-toggle" role="group" aria-label="Map data source"><button className={mode==='live'?'selected':''} aria-pressed={mode==='live'} onClick={()=>onMode('live')}>Live feed</button><button className={mode==='archive'?'selected':''} aria-pressed={mode==='archive'} onClick={()=>onMode('archive')}>Archive search</button></div></div>
    {mode==='live'?<>
      <div className="live-controls"><label>Recent window<select aria-label="Recent hotspot window" value={feed.hours} onChange={e=>feed.setHours(+e.target.value)}><option value={24}>Last 24 hours</option><option value={72}>Last 72 hours</option><option value={168}>Last 7 days</option></select></label><label className="live-quality"><input type="checkbox" checked={feed.quality} onChange={e=>feed.setQuality(e.target.checked)}/>Nominal + high confidence</label><button className="button" disabled={busy||drawPending} onClick={feed.refresh}><RefreshCw size={14}/>{busy?'Updating…':'Refresh feed'}</button><button className="button" disabled={!data||busy} onClick={feed.exportCsv}><Download size={14}/>Recent CSV</button></div>
      {drawPending?<p role="status">Draw an area to load its recent detections.</p>:<>
        <div className="live-metrics" aria-live="polite"><div><span>Detections · last {feed.hours===168?'7 days':feed.hours+' hours'}</span><strong>{count(data?.count)}</strong></div><div><span>Today · UTC so far</span><strong>{count(data?.today_count)}</strong></div><div><span>Latest observation</span><strong className="live-time">{stamp(data?.latest_observation)}</strong></div></div>
        <p className="live-freshness" role="status">{busy?'Checking NASA FIRMS…':data?`${error?'Update failed · previous result shown':data.stale?'Cached feed · update delayed':data.partial?'Partial feed · some sources unavailable':'Live'} · Updated ${stamp(data.fetched_at)}`:error?'Feed unavailable · use Refresh feed to retry.':'Connecting to NASA FIRMS…'}</p>
        {data&&data.count===0&&<p>{data.partial?'No detections returned by the available sources in this window. Other sources are unavailable.':'No matching detections.'}</p>}
        {data?.truncated&&<p>Showing the {count(data.returned)} most recent of {count(data.count)} detections. CSV includes all matching records.</p>}
        {error&&<p role="alert" className="notice warning">{error}</p>}
        {data?.sources?.some((s:Data)=>s.status!=='current')&&<p className="notice warning">{data.sources.filter((s:Data)=>s.status!=='current').map((s:Data)=>`${s.product.replace('_NRT','')}: ${s.status==='stale'?'cached data':'unavailable'}`).join(' · ')}. Counts may be incomplete.</p>}
      </>}
      {feed.exportError&&<p role="alert" className="notice warning">{feed.exportError}</p>}
      
      <div className="live-legend"><span><i/>Within 24 hours</span><span><i/>Earlier in selected window</span></div>
    </>:null}
  </section>;
}

import {CalendarDays,Clock3,Flame,MapPin,Moon,Satellite,ShieldCheck,ShieldQuestion,Sun} from 'lucide-react';
import './hotspotPopup.css';

const number=(v:any)=>v!==null&&v!==undefined&&String(v).trim()!==''&&Number.isFinite(Number(v))?Number(v):null;

export default function HotspotPopup({point:p}:{point:Record<string,any>}) {
  const sensor=String(p.sensor||'Satellite').toUpperCase();
  const raw=p.sensor_confidence??p.confidence_raw??p.confidence;
  const code=String(raw??'').trim().toLowerCase();
  const score=number(raw);
  const level=({'n':'nominal','nominal':'nominal','h':'high','high':'high','l':'low','low':'low'} as Record<string,string>)[code]
    ||(sensor==='MODIS'&&score!==null&&score>=0&&score<=100?(score>=80?'high':score>=30?'nominal':'low'):'unknown');
  const confidence={high:'High',nominal:'Nominal',low:'Low',unknown:'Unavailable'}[level]||'Unavailable';
  const bars=level==='high'?3:level==='nominal'?2:level==='low'?1:0;
  const frp=number(p.frp);
  const validFrp=frp!==null&&frp>=0;
  const power=validFrp?frp.toLocaleString('en-AU',{notation:frp>=1000?'compact':frp>0&&frp<.0001?'scientific':'standard',maximumFractionDigits:frp>0&&frp<1?4:2}):'—';
  const fullStamp=p.timestamp||p.acquisition_datetime_utc||(String(p.time||'').includes('T')?p.time:null);
  const stamp=fullStamp?new Date(fullStamp):p.date?new Date(String(p.date).slice(0,10)+'T00:00:00Z'):null;
  const validDate=stamp&&!Number.isNaN(stamp.getTime());
  const date=validDate?stamp.toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}):'Date unavailable';
  const timeCode=String(p.time||'').replace(':','');
  const time=fullStamp&&validDate?stamp.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'UTC'}):/^\d{4}$/.test(timeCode)&&+timeCode.slice(0,2)<24&&+timeCode.slice(2)<60?`${timeCode.slice(0,2)}:${timeCode.slice(2)}`:null;
  const daynight=String(p.daynight||'').toUpperCase();
  const lat=number(p.lat??p.latitude),lon=number(p.lon??p.longitude);
  const coordinate=(value:number,positive:string,negative:string)=>`${Math.abs(value).toFixed(4)}° ${value>=0?positive:negative}`;
  const ConfidenceIcon=level==='unknown'?ShieldQuestion:ShieldCheck;
  return <article className={`hotspot-card hotspot-sensor-${sensor==='MODIS'?'modis':'viirs'}`} aria-label={`${sensor} hotspot details`}>
    <header className="hotspot-card-heading"><span className="hotspot-symbol"><Flame size={22} strokeWidth={1.8}/></span><div><span className="hotspot-eyebrow">SATELLITE DETECTION</span><h3>{sensor} hotspot</h3></div></header>
    <div className="hotspot-platform"><Satellite size={15}/><strong>{p.satellite||'Satellite unspecified'}</strong><span>NASA FIRMS</span></div>
    <div className="hotspot-observed"><span><CalendarDays size={15}/>{date}</span><span><Clock3 size={15}/><strong>{time||'—'}</strong><small>UTC</small></span></div>
    <div className="hotspot-metrics">
      <section className={`hotspot-power ${validFrp?'':'hotspot-no-value'}`} title="Fire radiative power measured by the satellite. This is not a fire-danger rating."><span className="hotspot-metric-label"><Flame size={15}/>Radiative power</span><div className="hotspot-power-value">{power}<small>{validFrp?'MW':'Unavailable'}</small></div><span className="hotspot-metric-footnote">FRP</span></section>
      <section className={`hotspot-confidence hotspot-confidence-${level}`} title="Native sensor confidence category; not the probability of a fire."><span className="hotspot-metric-label"><ConfidenceIcon size={15}/>Confidence</span><div className="hotspot-confidence-bars" aria-hidden="true">{[1,2,3].map(i=><i key={i} className={i<=bars?'is-filled':''}/>)}</div><strong>{confidence}</strong>{sensor==='MODIS'&&score!==null&&score>=0&&score<=100&&<span className="hotspot-confidence-score">{score}/100 native score</span>}</section>
    </div>
    <footer className="hotspot-card-footer"><span className="hotspot-pass">{daynight==='D'?<><Sun size={14}/>Daytime</>:daynight==='N'?<><Moon size={14}/>Nighttime</>:<><Clock3 size={14}/>Pass unknown</>}</span>{lat!==null&&lon!==null&&<span className="hotspot-coordinates"><MapPin size={13}/><span>{coordinate(lat,'N','S')}<br/>{coordinate(lon,'E','W')}</span></span>}</footer>
  </article>;
}

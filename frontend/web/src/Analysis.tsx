import { lazy, Suspense, useEffect, useRef, useState, useMemo } from 'react';
import { Download, RefreshCw, Info } from 'lucide-react';
import { toPng } from 'html-to-image';
import { get } from './api';
import { AUSTRALIA_BOUNDS, REGIONS, MONTHS, australiaBoundsError, monthDateRange, csv } from './analytics.mjs';
import './analysis.css';
import LiveHotspots, {useLiveHotspots} from './LiveHotspots';
import ActivitySummary from './ActivitySummary';
import HelpHint from './HelpHint';

const AreaMap = lazy(() => import('./Map'));
type Data = Record<string, any>;
const fmt = (n: any, d = 0) => n == null ? '—' : Number(n).toLocaleString('en-AU', { maximumFractionDigits: d });
const panels = ['Calendar', 'Anomalies', 'Seasons & trends', 'Daily observations', 'Harmonization', 'Forecast', 'Climate'] as const;
type Panel = typeof panels[number];
const panelHelp:Record<string,string>={
  Calendar:'Monthly satellite detections on a logarithmic color scale. M: calibrated model estimate; O: observation; S: scaled reference. Partial months (*) have no full-month anomaly. Hotspot counts are not unique fires or burned area.',
  Anomalies:'Colors and robust z scores compare each complete month with the same calendar month in the selected baseline. A dash means the baseline is missing or has zero variability. Partial months are excluded.',
  'Seasons & trends':'Seasonal summaries use the selected baseline. Annual rankings and trends use complete years. These patterns describe recorded activity and do not establish its cause.',
  'Daily observations':'Native observations are shown for the selected UTC day and month. Monthly calibration is not applied to daily detections. No detections does not establish no fire.',
  Harmonization:'Native MODIS, calibrated MODIS and the VIIRS reference use the same area and monthly support. Production comparisons can include training years; agreement is not independently verified fire accuracy.',
  Forecast:'Experimental one-to-three-month outlook using calibrated MODIS history. Positive skill beats historical climatology. Nonpositive skill normally uses the historical range when enough baseline data exist. This is not operational fire-spread guidance.',
  Climate:'NASA POWER and NOAA climate context. Anomalies are in standard deviations; negative soil-wetness values indicate drier conditions. These are not fire-risk scores.'
};

function Note({ children }: { children: React.ReactNode }) {
  return <div className="notice"><Info size={17}/><div>{children}</div></div>;
}
function Table({ rows, columns }: { rows: Data[]; columns: string[] }) {
  if (!rows?.length) return <p className="empty-state">No results available for this selection.</p>;
  return <div className="table-scroll"><table><thead><tr>{columns.map(c => <th key={c}>{c.replaceAll('_', ' ')}</th>)}</tr></thead><tbody>{rows.map((r, i) => <tr key={i}>{columns.map(c => <td key={c}>{typeof r[c] === 'number' ? (c==='year'||c==='month'?String(r[c]):fmt(r[c], 2)) : String(r[c] ?? '—')}</td>)}</tr>)}</tbody></table></div>;
}
function LineChart({ rows, keys, label, x = 'label' }: { rows: Data[]; keys: string[]; label: string; x?: string }) {
  if (!rows.length) return null;
  const max = Math.max(1, ...rows.flatMap(r => keys.map(k => Number(r[k]) || 0)));
  const colors = ['#286c70', '#d76532', '#8a799b', '#547a9a'];
  const labels: Record<string,string> = {modis_raw:'MODIS',viirs_raw:'VIIRS reference',modis_harmonized:'Harmonized',reference:'VIIRS reference (test)',prediction:'Harmonized (independent test)',p10:'Lower 80% bound',p90:'Upper 80% bound',p50:'Median',snpp:'S-NPP',noaa20:'NOAA-20',noaa21:'NOAA-21'};
  return <><div className="chart-key">{keys.map((k, i) => <span style={{ color: colors[i % colors.length] }} key={k}>{labels[k] || k.replaceAll('_', ' ')}</span>)}</div><svg viewBox="0 0 900 260" role="img" aria-label={label} className="analysis-chart">
    <title>{label}</title><rect width="900" height="260" fill="white"/>
    {[0, .5, 1].map(t => <g key={t}><line x1="90" x2="870" y1={215 - t * 180} y2={215 - t * 180} stroke="#e5e9e9"/><text x="5" y={219 - t * 180} fontSize="12" fill="#546969">{fmt(max * t)}</text></g>)}
    {keys.map((k, c) => <path key={k} stroke={colors[c % colors.length]} strokeWidth="2.5" fill="none" d={rows.map((r, i) => r[k] == null ? '' : `${i === 0 || rows[i - 1][k] == null ? 'M' : 'L'} ${90 + i / Math.max(rows.length - 1, 1) * 780} ${215 - r[k] / max * 180}`).join(' ')}/>)}
    {rows.filter((_, i) => i % Math.max(1, Math.ceil(rows.length / 8)) === 0).map((r, i) => <text key={i} x={90 + rows.indexOf(r) / Math.max(rows.length - 1, 1) * 780} y="247" fontSize="12" fill="#546969">{r[x]}</text>)}
  </svg></>;
}
async function post(path: string, body: Data, signal?: AbortSignal) {
  const r = await fetch('/api/analysis/' + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : `Analysis request failed (${r.status})`);
  }
  return r.json();
}

export default function Analysis() {
  const [readiness, setReadiness] = useState<Data | null>(null), [setupError, setSetupError] = useState('');
  const [meta, setMeta] = useState<Data | null>(null), [revision, setRevision] = useState(0);
  const [mapMode,setMapMode]=useState<'live'|'archive'>('live');
  const [preset, setPreset] = useState('australia'), [mode, setMode] = useState('region');
  const [bounds, setBounds] = useState<number[]>(AUSTRALIA_BOUNDS.slice());
  const [geometry, setGeometry] = useState<any>(null);
  const [point, setPoint] = useState({ lon: '133.9', lat: '-23.7', radius: '100' });
  const [start, setStart] = useState('2000-11'), [end, setEnd] = useState('');
  const [b0, setB0] = useState('2003'), [b1, setB1] = useState('2022');
  const baselineMin=meta?.baseline_limits?.min ?? 2000, baselineMax=meta?.baseline_limits?.max ?? new Date().getFullYear();
  const validBaseline=Number.isInteger(+b0)&&Number.isInteger(+b1)&&+b0>=baselineMin&&+b1<=baselineMax&&+b0<=+b1;
  const clampYear=(value:string,min:number,max:number)=>String(Math.max(min,Math.min(max,Number.isFinite(+value)&&value!==''?Math.trunc(+value):min)));
  const [series, setSeries] = useState('modis_harmonized'), [panel, setPanel] = useState<Panel>('Calendar');
  const [result, setResult] = useState<Data | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [selected, setSelected] = useState(''), [hotStart, setHotStart] = useState(''), [hotEnd, setHotEnd] = useState('');
  const [hot, setHot] = useState<Data | null>(null), [hotError, setHotError] = useState(''), [hotBusy, setHotBusy] = useState(false);
  const [exportError, setExportError] = useState('');
  const [day,setDay] = useState(''), [daily,setDaily] = useState<Data|null>(null), [dailyBusy,setDailyBusy]=useState(false), [dailyError,setDailyError]=useState('');
  const dailyController=useRef<AbortController|null>(null);
  const controller = useRef<AbortController | null>(null), hotController = useRef<AbortController | null>(null);
  const chart = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const c = new AbortController(); setSetupError('');
    get<Data>('analysis/readiness', c.signal).then(async d => {
      if (c.signal.aborted) return;
      setReadiness(d);
      if (d.ready) {
        const m = await get<Data>('analysis/meta', c.signal);
        if (!c.signal.aborted) { setMeta(m); setEnd(m.latest_month); setDay(m.latest_month+'-01'); }
      }
    }).catch(e => { if (!c.signal.aborted) setSetupError(e.message); });
    return () => c.abort();
  }, [revision]);
  // Any edited selection invalidates results and aborts in-flight work.
  useEffect(() => {
    controller.current?.abort(); hotController.current?.abort(); dailyController.current?.abort();
    setDaily(null); setDailyBusy(false); setDailyError('');
    setResult(null); setHot(null); setBusy(false); setHotBusy(false); setError(''); setHotError('');
  }, [preset, mode, bounds, geometry, point, start, end, b0, b1, series]);
  useEffect(() => () => { controller.current?.abort(); hotController.current?.abort(); dailyController.current?.abort(); }, []);
  const aoi = mode === 'region' && preset === 'australia' ? { type: 'region', region: 'Australia' }
    : mode === 'point' ? { type: 'point_radius', point: [+point.lon, +point.lat], radius_km: +point.radius }
    : mode === 'draw' && geometry ? { type: 'polygon', geometry } : { type: 'bbox', bbox: bounds };
  const live=useLiveHotspots(aoi,mapMode==='live' && (mode!=='draw'||!!geometry));
  const query = { aoi, start, ...(end ? { end } : {}), baseline_start: +b0, baseline_end: +b1, series };
  const region = REGIONS.find(r => r.id === preset) || REGIONS[1];
  const mapGeometry=useMemo(() => {
    if(mode==='draw') return geometry;
    if(mode!=='point') return null;
    const lon=+point.lon,lat=+point.lat,r=+point.radius;
    if(![lon,lat,r].every(Number.isFinite)||r<=0) return null;
    const ry=r/111.32,rx=ry/Math.max(Math.cos(lat*Math.PI/180),.01);
    return {type:'Polygon',coordinates:[Array.from({length:97},(_,i)=>{const a=i/96*2*Math.PI;return [lon+rx*Math.cos(a),lat+ry*Math.sin(a)];})]};
  },[mode,geometry,point]);
  const mapBounds=useMemo(() => mode==='point'&&mapGeometry ? [Math.min(...mapGeometry.coordinates[0].map((p:number[])=>p[0])),Math.min(...mapGeometry.coordinates[0].map((p:number[])=>p[1])),Math.max(...mapGeometry.coordinates[0].map((p:number[])=>p[0])),Math.max(...mapGeometry.coordinates[0].map((p:number[])=>p[1]))] : bounds,[mode,mapGeometry,bounds]);
  async function loadDay(selectedDay=day) {
    setMapMode('archive');
    if(!selectedDay) return;
    dailyController.current?.abort();const c=new AbortController();dailyController.current=c;
    setDailyBusy(true);setDailyError('');setDaily(null);setHot(null);setDay(selectedDay);setPanel('Daily observations');
    try {
      const range=monthDateRange(selectedDay,meta?.manifest?.firms_recent?.last_date);
      const [counts,points]=await Promise.all([
        post('daily',{aoi,start_date:range[0],end_date:range[1]},c.signal),
        post('hotspots',{aoi,start_date:selectedDay,end_date:selectedDay},c.signal),
      ]);
      if(!c.signal.aborted){setDaily(counts);setHot(points);setHotStart(selectedDay);setHotEnd(selectedDay);setSelected(selectedDay.slice(0,7));}
    } catch(e) {if(!c.signal.aborted)setDailyError((e as Error).message);}
    finally {if(!c.signal.aborted)setDailyBusy(false);}
  }
  function exportRows(name:string,rows:Data[]) {
    const enriched=rows.map(row=>({...row,area_name:cal?.aoi?.name,area_selection:JSON.stringify(aoi),method_version:cal?.method_version,unit:name.includes('independent')?'Reference/prediction: S-NPP-equivalent; raw_modis: native MODIS detections':'Native detections per platform'}));
    const url=URL.createObjectURL(new Blob([csv(enriched)],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function analyze(e: React.FormEvent) {
    e.preventDefault();
    if (mode !== 'point') { const issue = australiaBoundsError(bounds); if (issue) { setError(issue); return; } }
    controller.current?.abort(); const c = new AbortController(); controller.current = c;
    setBusy(true); setError(''); setResult(null);
    try {
      // Optional panels fail independently; a missing climate/forecast output does not hide the calendar.
      const cal = await post('calendar', query, c.signal);
      if (c.signal.aborted) return;
      setResult({ cal });
      const extras = await Promise.allSettled([
        post('forecast', { ...query, series: 'modis_harmonized' }, c.signal), post('compare', query, c.signal),
        post('drivers', aoi, c.signal),
      ]);
      if (!c.signal.aborted) {
        const data: Data = { cal }, errors: Data = {};
        extras.forEach((r, i) => { const key = ['fc', 'cmp', 'drv'][i]; if (r.status === 'fulfilled') data[key] = r.value; else errors[key] = r.reason.message; });
        setResult({ ...data, errors });
      }
    } catch (e) { if (!c.signal.aborted) setError((e as Error).message); }
    finally { if (!c.signal.aborted) setBusy(false); }
  }
  async function loadHotspots() {
    setMapMode('archive');
    hotController.current?.abort(); const c = new AbortController(); hotController.current = c;
    setHotBusy(true); setHot(null); setHotError('');
    try {
      const d = await post('hotspots', { aoi, ...(hotStart ? { start_date: hotStart } : {}), ...(hotEnd ? { end_date: hotEnd } : {}) }, c.signal);
      if (!c.signal.aborted) setHot(d);
    } catch (e) { if (!c.signal.aborted) setHotError((e as Error).message); }
    finally { if (!c.signal.aborted) setHotBusy(false); }
  }
  async function exportCsv(kind: string) {
    setExportError('');
    try {
      const body = kind === 'calendar' ? query : { aoi, start_date: hotStart || hot?.date_range?.[0], end_date: hotEnd || hot?.date_range?.[1] };
      const r = await fetch(`/api/analysis/export/${kind}.csv`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Export failed'); }
      const url = URL.createObjectURL(await r.blob()); const a = document.createElement('a'); a.href = url; a.download = `embercal-australia-${kind}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setExportError((e as Error).message); }
  }
  async function exportPng() {
    if (!chart.current) return;
    setExportError('');
    try { const href = await toPng(chart.current, { backgroundColor: '#fff', pixelRatio: 2 }); const a = document.createElement('a'); a.href = href; a.download = `embercal-${panel.toLowerCase().replaceAll(' ', '-')}.png`; a.click(); }
    catch { setExportError('PNG export could not be created in this browser. CSV export remains available.'); }
  }
  const cal = result?.cal, rows: Data[] = cal?.months || [];
  const chosen = rows.find(r => `${r.year}-${String(r.month).padStart(2, '0')}` === selected) || rows.at(-1);
  const years = [...new Set(rows.map(r => r.year))].reverse();
  const heatMax = Math.max(1, ...rows.map(r => r.value || 0));
  const areaName=mode==='region'?region.name:(cal?.aoi?.name || (mode==='point'?'Point and radius':mode==='draw'?'Custom polygon':'Custom bounding box'));
  const sourceCounts=rows.reduce((acc:Data,row:Data)=>{acc[row.value_kind]=(acc[row.value_kind]||0)+1;return acc;},{});
  const points = ((mapMode==='live'?live.data?.points:hot?.points) || []).map((r: Data) => ({ ...r, sensor: r.sensor.toLowerCase() }));
  return <>
    {setupError && <div className="notice warning" role="alert">{setupError}<button className="button" onClick={() => setRevision(r => r + 1)}>Retry connection</button></div>}
    {readiness && !readiness.ready && <section className="ec-card analysis-setup"><span className="eyebrow">NATIONAL DATA SETUP</span><h2>The Australia workspace is ready for its data.</h2><p>The supplied ZIP does not contain national processed outputs. Build the Australia record locally to activate these analyses. The recent hotspot feed can be used independently while historical outputs are unavailable.</p><ol><li>Run <code>.\Setup.ps1</code> to install the analysis dependencies.</li><li>Run <code>.\Build-Australia.ps1</code> to download and process Australian FIRMS and climate data.</li><li>Restart the backend and check readiness.</li></ol><p>First-run downloads and model training can be substantial. A server-side FIRMS key fills the current-year record; the public fallback provides only recent days.</p><p>Missing outputs: {readiness.missing_data.join(', ') || 'None'}. {readiness.missing_dependencies.length > 0 && `Missing dependencies: ${readiness.missing_dependencies.join(', ')}.`}</p><button className="button" onClick={() => setRevision(r => r + 1)}><RefreshCw size={15}/> Check readiness</button></section>}
    <div className="explorer-grid"><section className="ec-card"><h2>Your Australian area</h2><form className="analysis-form" onSubmit={analyze}>
      <label>Selection method<select value={mode} onChange={e => { setMode(e.target.value); setGeometry(null); }}><option value="region">Region preset</option><option value="bbox">Bounding box / coordinates</option><option value="point">Point + radius</option><option value="draw">Draw on map</option></select></label>
      <label>Australian region<select value={preset} onChange={e => { const r = REGIONS.find(r => r.id === e.target.value)!; setPreset(r.id); setBounds(r.bbox.slice()); setGeometry(null); }}>{REGIONS.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
      {mode === 'point' ? <div className="coordinate-grid">{(['lon', 'lat', 'radius'] as const).map(k => <label key={k}>{k === 'lon' ? 'Longitude' : k === 'lat' ? 'Latitude' : 'Radius (km)'}<input required type="number" step="any" min={k === 'radius' ? 1 : k === 'lat' ? -44.5 : 112} max={k === 'radius' ? 1000 : k === 'lat' ? -9 : 154.5} value={point[k]} onChange={e => setPoint({ ...point, [k]: e.target.value })}/></label>)}</div>
      : mode === 'bbox' ? <div className="coordinate-grid">{['West', 'South', 'East', 'North'].map((name, i) => <label key={name}>{name}<input required type="number" step="any" min={i % 2 ? -44.5 : 112} max={i % 2 ? -9 : 154.5} value={bounds[i]} onChange={e => setBounds(bounds.map((n, j) => i === j ? Number(e.target.value) : n))}/></label>)}</div> : mode === 'draw' ? <p>{geometry ? 'Area selected. Exact shape will be used.' : 'Choose rectangle, polygon or circle on the map.'}</p> : null}
      <div className="coordinate-grid"><label>From month<input required type="month" min="2000-11" max={end || undefined} value={start} onChange={e => setStart(e.target.value)}/></label><label>To month<input type="month" min={start} max={meta?.latest_month} value={end} onChange={e => setEnd(e.target.value)}/></label><label>Baseline from<input required type="number" min={baselineMin} max={Math.min(+b1||baselineMax,baselineMax)} value={b0} onChange={e => setB0(e.target.value)} onBlur={()=>setB0(clampYear(b0,baselineMin,Math.min(+b1||baselineMax,baselineMax)))}/></label><label>Baseline to<input required type="number" min={Math.max(+b0||baselineMin,baselineMin)} max={baselineMax} value={b1} onChange={e => setB1(e.target.value)} onBlur={()=>setB1(clampYear(b1,Math.min(Math.max(+b0||baselineMin,baselineMin),baselineMax),baselineMax))}/></label></div>
      <label>Series<select value={series} onChange={e => setSeries(e.target.value)}><option value="modis_harmonized">Harmonized</option><option value="modis_raw">MODIS</option><option value="viirs_raw">VIIRS reference</option></select></label>
      <button className="button primary" disabled={busy || !readiness?.ready || !meta || !validBaseline} type="submit"><RefreshCw size={15}/>{busy ? 'Analysing Australia…' : 'Analyse this area'}</button>
    </form></section><div className="explorer-map"><LiveHotspots feed={live} mode={mapMode} onMode={setMapMode} drawPending={mode==='draw'&&!geometry}/><Suspense fallback={<div className="empty-state">Loading Australia map…</div>}><AreaMap region={region} records={points} observed feedLabel={mapMode==='live'?'NASA FIRMS near-real-time · UTC':'Saved archive observations · UTC'} bounds={mapBounds} selectionGeometry={mapGeometry} allowDrawing={mode === 'draw'} exactDrawing onArea={b => { if (mode === 'draw') setBounds(b); }} onGeometry={setGeometry}/></Suspense></div></div>
    <section className="ec-card"><div className="ec-card-heading"><h2>Hotspots in this area</h2><HelpHint label="About archive search">Search the saved Australian archive. Blank dates use its latest seven available days. CSV includes all matching records; the map and table may show fewer.</HelpHint></div><div className="ec-toolbar"><label>From date<input type="date" value={hotStart} onChange={e => { hotController.current?.abort(); setHot(null); setHotBusy(false); setHotStart(e.target.value); }}/></label><label>To date<input type="date" value={hotEnd} onChange={e => { hotController.current?.abort(); setHot(null); setHotBusy(false); setHotEnd(e.target.value); }}/></label><button className="button" disabled={!readiness?.ready || hotBusy || (mode === 'draw' && !geometry)} onClick={loadHotspots}>{hotBusy ? 'Loading…' : 'Show hotspots'}</button><button className="button" disabled={!hot?.count} onClick={() => exportCsv('hotspots')}><Download size={15}/> Hotspot CSV</button></div>{hotError && <p role="alert" className="notice warning">{hotError}</p>}{hot && <><p>{fmt(hot.count)} detections · {fmt(hot.returned)} shown{hot.truncated ? ' (map capped at highest FRP)' : ''} · {hot.date_range?.join(' to ')}.</p><Table rows={hot.points.slice(0, 100)} columns={['date', 'time', 'sensor', 'satellite', 'lat', 'lon', 'frp', 'confidence']}/></>}</section>
    <ActivitySummary aoi={aoi} areaName={areaName} baselineStart={+b0} baselineEnd={+b1} series={series} latestComplete={meta?.latest_complete_month} enabled={!!readiness?.ready&&validBaseline} pending={mode==='draw'&&!geometry}/>
    {error && <div className="notice warning" role="alert">{error}</div>}
    {cal && <>
      <div className="source-summary"><div><span className="eyebrow">SELECTED RECORD</span><h2>{cal.series_info?.label || 'Harmonized'}</h2></div><div className="source-tags"><span className="tag-model">{sourceCounts.model_estimate||0} model months</span><span className="tag-observed">{sourceCounts.observed_count||0} observed months</span><span className="tag-scaled">{sourceCounts.scaled_reference||0} scaled-reference months</span></div></div>
      {cal.aoi?.warning && <Note>{cal.aoi.warning}</Note>}
      <div className="ec-toolbar analysis-tabs"><div className="ec-toggle">{panels.map(p => <button key={p} className={panel === p ? 'selected' : ''} onClick={() => setPanel(p)}>{p}</button>)}</div><button className="button" onClick={() => exportCsv('calendar')}><Download size={15}/> Calendar CSV</button><button className="button" onClick={exportPng}><Download size={15}/> PNG</button></div>
      {exportError && <p role="alert" className="notice warning">{exportError}</p>}
      <section className="ec-card analysis-output" ref={chart}><span className="eyebrow">{areaName} · {start}–{end || meta?.latest_month} · BASELINE {b0}–{b1}</span><div className="ec-card-heading"><h2>{panel}</h2><HelpHint label="About this analysis">{panelHelp[panel]}</HelpHint></div><p className="caption">{cal.series_info?.label} · {cal.unit}</p>
        {(panel === 'Calendar' || panel === 'Anomalies') && <><div className="heat-scroll"><div className="heat-grid"><span/>{MONTHS.map(m => <span className="month-label" key={m}>{m}</span>)}{years.map(y => <span className="analysis-year" key={y}><strong className="year-label">{y}</strong>{MONTHS.map((_, i) => {
          const r = rows.find(r => r.year === y && r.month === i + 1), anomaly = panel === 'Anomalies';
          const v = anomaly ? r?.z_robust : r?.value, light = anomaly ? 96 - Math.min(4, Math.abs(v || 0)) * 12 : 94 - 65 * Math.log1p(v || 0) / Math.log1p(heatMax);
          const color = v == null ? '#f0f2f2' : `hsl(${anomaly ? v < 0 ? 210 : 17 : 173} ${anomaly ? 55 : 29}% ${light}%)`;
          const key = `${y}-${String(i + 1).padStart(2, '0')}`;
          return <button key={i} className={"heat-cell source-"+(r?.value_kind||"unavailable")} disabled={!r} style={{ background: color, color: light < 55 ? '#fff' : '#173a3c' }} aria-pressed={chosen === r} aria-label={`${key}: ${fmt(v, anomaly ? 1 : 0)} ${anomaly ? 'robust z score' : cal.unit}${r?.partial ? ', partial month' : ''}`} title={`${key} · ${r?.label || ''} · ${r?.method || ''}`} onClick={() => setSelected(key)}>{fmt(v, anomaly ? 1 : 0)}{r?.partial ? '*' : ''}<small className="cell-source">{r?.value_kind==='model_estimate'?'M':r?.value_kind==='scaled_reference'?'S':r?.value_kind==='observed_count'?'O':''}</small></button>;
        })}</span>)}</div></div><p className="caption">{panel === 'Anomalies' ? 'Blue: below · Orange: above' : 'Hotspot counts · Log scale'} · M Model · O Observed · S Scaled · * Partial</p>
        {chosen && <div className="analysis-detail"><div className="ec-card-heading"><h3>{MONTHS[chosen.month-1]} {chosen.year}</h3><HelpHint label="About this month's data">{chosen.uncertainty_note} Boundary counts are area-weighted approximations; satellite viewing coverage is unknown.</HelpHint><button className="button" disabled={chosen.value==null} onClick={()=>loadDay(`${chosen.year}-${String(chosen.month).padStart(2,'0')}-01`)}>Inspect daily observations</button></div><p><strong>{fmt(chosen.value,1)}</strong> · {chosen.value_kind?.replaceAll('_',' ')} · {chosen.partial?'Partial month — anomaly withheld':chosen.label||'No anomaly classification'}</p>
        {chosen.lo!=null&&chosen.hi!=null ? <p>Approximate residual range: {fmt(chosen.lo,1)}–{fmt(chosen.hi,1)}</p> : null}
        <p>Percentile {fmt(chosen.percentile,1)} · {fmt(chosen.percent_of_median,1)}% of median · {chosen.baseline_n_years} baseline years{chosen.baseline_first_year?` (${chosen.baseline_first_year}–${chosen.baseline_last_year})`:''}.</p><p className="caption">Source: {chosen.sensor||'Unavailable'}</p></div>}</>}
        {panel==='Daily observations'&&<><div className="ec-toolbar"><label>Observation day (UTC)<input type="date" min="2000-11-01" max={meta?.manifest?.firms_recent?.last_date} value={day} onChange={e=>{dailyController.current?.abort();setDaily(null);setHot(null);setDailyBusy(false);setDay(e.target.value);}}/></label><button className="button primary" disabled={dailyBusy||!day} onClick={()=>loadDay()}>{dailyBusy?'Loading day…':'Load day and month'}</button><button className="button" disabled={!daily?.days?.length} onClick={()=>exportRows('embercal-daily-observations.csv',daily?.days||[])}>Daily CSV</button></div>{dailyError&&<p role="alert" className="notice warning">{dailyError}</p>}{daily&&<><LineChart rows={daily.days.map((r:Data)=>({...r,label:r.date.slice(8)}))} keys={['modis','snpp','noaa20','noaa21']} label="Daily native-sensor detections in the selected month"/><p>{hot?.count==null?'':`${fmt(hot.count)} detections on ${day}; ${fmt(hot.returned)} locations shown on the map above.`}</p><Table rows={daily.days} columns={['date','modis','snpp','noaa20','noaa21','viewing_coverage']}/></>}</>}
        {panel === 'Seasons & trends' && <><LineChart rows={(cal.climatology || []).map((r: Data) => ({ ...r, label: r.name }))} keys={['median', 'p10', 'p90']} label="Baseline monthly activity and 80 percent historical range"/><p>Typical active months: {(cal.season?.season_months || []).map((m: number) => MONTHS[m - 1]).join(', ') || 'No season identified'}. Critical months: {(cal.season?.critical_months || []).map((m: number) => MONTHS[m - 1]).join(', ') || 'Unavailable'}.</p><LineChart rows={(cal.annual || []).filter((r: Data) => r.complete).map((r: Data) => ({ ...r, label: r.year }))} keys={['total']} label="Complete-year annual hotspot totals"/>{cal.trend?.available ? <p>{cal.trend.direction} · Sen slope {fmt(cal.trend.sen_slope_per_year, 2)} per year · 95% interval {cal.trend.slope_ci95.map((v: number) => fmt(v, 2)).join(' to ')} · Mann–Kendall p={cal.trend.p_value}.</p> : <p>{cal.trend?.reason}</p>}<h3>Highest complete years</h3><Table rows={cal.rankings?.top_years} columns={['year', 'total']}/><h3>Highest complete months</h3><Table rows={cal.rankings?.top_months} columns={['year', 'month', 'value']}/></>}
        {panel === 'Forecast' && (result?.fc?.available ? <><span className="analysis-status-chip">Experimental outlook · Issued {result.fc.issued_from_data_through}</span>{result.fc.warning && <p className="notice warning">{result.fc.warning}</p>}<LineChart rows={result.fc.forecasts.map((r: Data) => ({ ...r, label: r.target }))} keys={['p50', 'p10', 'p90']} label="Forecast median and 80 percent predictive range"/>{result.fc.forecasts.map((r: Data) => <div className="analysis-detail" key={r.lead}><h3>{r.target} · {r.source === 'climatology' ? 'Historical baseline outlook' : 'Model outlook'}</h3><p>Median {fmt(r.p50)} · 80% range {fmt(r.p10)}–{fmt(r.p90)} · skill {fmt(r.skill?.crpss == null ? null : r.skill.crpss * 100, 1)}% ({r.skill?.level}).</p><p>Below / near / above normal: {['below', 'near', 'above'].map(k => `${fmt(r.terciles?.[k] == null ? null : r.terciles[k] * 100, 1)}%`).join(' / ')}.</p></div>)}</> : <p>{result?.errors?.fc || result?.fc?.reason || 'Forecast output is not built. Run the forecast pipeline step.'}</p>)}
        {panel==='Harmonization'&&(result?.cmp ? <><h3>What the calibration changes</h3><LineChart rows={result.cmp.monthly.map((r:Data)=>({...r,label:`${r.year}-${String(r.month).padStart(2,'0')}`}))} keys={['modis_raw','modis_harmonized','viirs_raw']} label="MODIS, calibrated predictions and VIIRS reference"/><Table rows={result.cmp.monthly} columns={['year','month','modis_raw','modis_harmonized','viirs_raw','method']}/>
        </> : <p>{result?.errors?.cmp||'Comparison is loading.'}</p>)}
        {panel === 'Climate' && (result?.drv?.available ? <><p>ENSO: {result.drv.enso?.phase} (ONI {result.drv.enso?.oni}, {result.drv.enso?.as_of}) · IOD: {result.drv.iod?.phase} (DMI {result.drv.iod?.dmi}, {result.drv.iod?.as_of}).</p><Table rows={result.drv.monthly} columns={['year', 'month', 'GWETROOT_z', 'PRECTOTCORR_z', 'PREC3_z', 'T2M_z', 'RH2M_z', 'WS2M_z']}/></> : <p>{result?.errors?.drv || 'Climate outputs are not built. Run the drivers pipeline step.'}</p>)}

      </section>
    </>}
  </>;
}

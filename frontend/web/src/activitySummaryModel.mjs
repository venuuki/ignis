export const MONTH_NAMES=['January','February','March','April','May','June','July','August','September','October','November','December'];
const finite=n=>typeof n==='number'&&Number.isFinite(n);

export function seasonView(data) {
  const climate=data?.climatology||[];
  const medians=climate.reduce((sum,c)=>sum+(finite(c.median)?c.median:0),0);
  const basis=medians>0?'median':'mean';
  const months=Array.from({length:12},(_,i)=>{
    const c=climate.find(c=>c.month===i+1);
    return {month:i+1,name:MONTH_NAMES[i],value:c&&finite(c[basis])?c[basis]:null,years:c?.n_years||0};
  });
  const max=Math.max(0,...months.map(m=>m.value||0));
  const active=data?.season?.season_months||[];
  const enough=months.some(m=>m.years>0);
  const peak=data?.season?.has_season?data.season.peak_month:null;
  const title=!enough?'Baseline unavailable':!max?'Little recorded activity':active.length===12?'Activity throughout the year':active.length===1?MONTH_NAMES[active[0]-1]:active.length?`${MONTH_NAMES[active[0]-1]} – ${MONTH_NAMES[active.at(-1)-1]}`:'No clear active window';
  return {months,max,basis,active,peak,title,enough,limited:months.some(m=>m.years<5)};
}

export function anomalyView(latest) {
  const available=latest&&!latest.partial&&finite(latest.count);
  const percentile=available&&finite(latest.percentile)?Math.max(0,Math.min(100,latest.percentile)):null;
  const years=latest?.baseline_n_years||0;
  const supported=percentile!==null&&years>=5;
  let label=available?'Baseline unavailable':'No complete-month data',tone='unknown',direction=0;
  if(percentile!==null&&!supported)label='Limited baseline';
  if(supported){
    if(percentile<10){label='Unusually low';tone='low';direction=-1;}
    else if(percentile<33.3){label='Lower than usual';tone='low';direction=-1;}
    else if(percentile<66.7){label='Near usual';tone='usual';}
    else if(percentile<90){label='Higher than usual';tone='high';direction=1;}
    else{label='Unusually high';tone='high';direction=1;}
  }
  const ratio=available&&finite(latest.median)&&latest.median>0?latest.count/latest.median:null;
  return {available,percentile,years,supported,label,tone,direction,ratio};
}

export function arcPath(start,end,r=83,cx=120,cy=120) {
  const point=p=>{const a=Math.PI*(1-p/100);return [cx+r*Math.cos(a),cy-r*Math.sin(a)];};
  const [x,y]=point(start),[u,v]=point(end);
  return `M ${x} ${y} A ${r} ${r} 0 ${end-start>100?1:0} 1 ${u} ${v}`;
}

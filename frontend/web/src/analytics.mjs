export const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
export const AUSTRALIA_BOUNDS = [112, -44.5, 154.5, -9];
export const REGIONS = [
  { id: 'study', name: 'Southeastern Australia', center: [148,-35], zoom: 5.4, bbox: [145,-38,151,-32] },
  { id: 'australia', name: 'Australia · national analysis', center: [134,-27], zoom: 3.2, bbox: AUSTRALIA_BOUNDS },
  { id: 'top_end', name: 'Top End / Kakadu', center: [132.5,-13.3], zoom: 6, bbox: [131,-14.5,134,-12] },
  { id: 'southwest', name: 'Southwestern Australia', center: [118,-32], zoom: 5.4, bbox: [115,-35,121,-29] },
  { id: 'queensland', name: 'Eastern Queensland', center: [148,-23], zoom: 5, bbox: [144,-28,153,-18] },
];
export function australiaBoundsError(b) {
  if (!Array.isArray(b) || b.length !== 4 || !b.every(Number.isFinite)) return 'Enter four valid coordinates.';
  const [w,s,e,n]=b;
  if (w >= e || s >= n) return 'West must precede east and south must precede north.';
  return w < 112 || e > 154.5 || s < -44.5 || n > -9 ? 'Select an area inside the Australia study extent (112–154.5°E, 44.5–9°S).' : '';
}
export function csv(rows) {
  if (!rows.length) return '';
  const keys = [...new Set(rows.flatMap(Object.keys))];
  const quote = v => '"' + String(v ?? '').replaceAll('"','""') + '"';
  return [keys.map(quote).join(','), ...rows.map(r => keys.map(k => quote(r[k])).join(','))].join('\n');
}
// Retrospective description of the selected interval; exclude selected year.
export function context(rows, selected, metric) {
  const values = rows.filter(r => r.month.slice(5) === selected.month.slice(5) && r.year !== selected.year)
    .map(r => r[metric]).filter(Number.isFinite).sort((a,b) => a-b);
  if (!values.length) return { n: 0, median: null, ratio: null };
  const i = Math.floor(values.length/2);
  const median = values.length % 2 ? values[i] : (values[i-1]+values[i])/2;
  return { n: values.length, median, ratio: median === 0 ? null : selected[metric]/median };
}

export function monthDateRange(day, lastAvailable) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error('Choose a valid UTC date.');
  const date = new Date(day+'T00:00:00Z');
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0,10)!==day) throw new Error('Choose a valid UTC date.');
  const first=day.slice(0,7)+'-01';
  const end=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).toISOString().slice(0,10);
  if (lastAvailable && day>lastAvailable) throw new Error('The selected day is after the available data.');
  return [first,lastAvailable && lastAvailable<end ? lastAvailable : end];
}

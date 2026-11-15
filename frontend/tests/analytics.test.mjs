import test from 'node:test';
import assert from 'node:assert/strict';
import { context, csv, REGIONS, australiaBoundsError } from '../web/src/analytics.mjs';
import { monthDateRange } from '../web/src/analytics.mjs';

test('daily inspection respects leap years and the last available observation date', () => {
  assert.deepEqual(monthDateRange('2024-02-15'), ['2024-02-01','2024-02-29']);
  assert.deepEqual(monthDateRange('2026-11-10','2026-11-14'), ['2026-11-01','2026-11-14']);
  assert.throws(()=>monthDateRange('2024-02-30'));
  assert.throws(()=>monthDateRange('2026-11-20','2026-11-14'));
});

test('all prototype presets stay within Australia and invalid coordinates fail', () => {
  for (const region of REGIONS) assert.equal(australiaBoundsError(region.bbox), '');
  assert.ok(australiaBoundsError([80, 7, 81, 8]));
  assert.ok(australiaBoundsError([151, -38, 145, -32]));
  assert.ok(australiaBoundsError([NaN, -38, 151, -32]));
});
test('context excludes selected year and other months', () => {
  const rows = [{month:'2019-01',year:2019,viirs:10},{month:'2020-01',year:2020,viirs:20},{month:'2021-01',year:2021,viirs:100},{month:'2019-02',year:2019,viirs:999}];
  assert.deepEqual(context(rows, rows[2], 'viirs'), {n:2, median:15, ratio:100/15});
  assert.deepEqual(context([rows[0]], rows[0], 'viirs'), {n:0, median:null, ratio:null});
});
test('zero historical median cannot produce infinite ratio', () => {
  const rows = [{month:'2019-01',year:2019,viirs:0},{month:'2020-01',year:2020,viirs:10}];
  assert.equal(context(rows,rows[1],'viirs').ratio,null);
});
test('CSV preserves heterogeneous backend fields and escapes text', () => {
  assert.equal(csv([{a:'a,"b'},{a:2,b:3}]), '"a","b"\n"a,""b",""\n"2","3"');
});

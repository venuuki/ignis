"""Recent native FIRMS detections, separate from the frozen monthly archive.

National per-product caches expire after five minutes, including today's data.
Failures never become zero-detection success responses. Secrets never leave this module.
"""
from __future__ import annotations
import csv
import io
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
import numpy as np
import pandas as pd
import shapely
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from shapely.geometry import shape

from .aoi import aoi_geometry
from .schemas import AOI
from .scope import validate_australia

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / 'data' / 'raw' / 'live_firms'
BBOX = (112, -44.5, 154.5, -9)
TTL = 300
PRODUCTS = {
    'MODIS_NRT': ('MODIS', 'MODIS', 'modis-c6.1', 'MODIS_C6_1'),
    'VIIRS_SNPP_NRT': ('VIIRS', 'S-NPP', 'suomi-npp-viirs-c2', 'SUOMI_VIIRS_C2'),
    'VIIRS_NOAA20_NRT': ('VIIRS', 'NOAA-20', 'noaa-20-viirs-c2', 'J1_VIIRS_C2'),
    'VIIRS_NOAA21_NRT': ('VIIRS', 'NOAA-21', 'noaa-21-viirs-c2', 'J2_VIIRS_C2'),
}
router = APIRouter(prefix='/api/analysis/live', tags=['Recent FIRMS detections'])
_cache = {}
_lock = threading.Lock()
_failed = {}


class LiveQuery(BaseModel):
    aoi: AOI = Field(default_factory=lambda: AOI(type='region', region='Australia'))
    hours: Literal[24, 72, 168] = 24
    quality_only: bool = True
    limit: int = Field(20000, ge=1, le=50000)


def utcnow():
    return datetime.now(timezone.utc)


def _read_csv(body):
    frame = pd.read_csv(io.StringIO(body), dtype={'acq_time': str, 'confidence': str})
    if not {'latitude', 'longitude', 'acq_date', 'acq_time', 'confidence'}.issubset(frame.columns):
        raise ValueError('Malformed FIRMS response')
    return frame


def _download(product, hours, now, key):
    """No permanently cached recent windows: fetch again when the live TTL expires."""
    with httpx.Client(timeout=httpx.Timeout(35, connect=10), follow_redirects=True) as client:
        if not key:
            _, _, folder, prefix = PRODUCTS[product]
            suffix = '24h' if hours == 24 else '7d'
            url = f'https://firms.modaps.eosdis.nasa.gov/active_fire/{folder}/csv/{prefix}_Australia_NewZealand_{suffix}.csv'
            r = client.get(url); r.raise_for_status()
            return _read_csv(r.text)
        frames = []
        day = (now - timedelta(hours=hours)).date()
        while day <= now.date():
            days = min(5, (now.date()-day).days+1)
            from urllib.parse import quote
            bbox = ','.join(str(v) for v in BBOX)
            url = f'https://firms.modaps.eosdis.nasa.gov/api/area/csv/{quote(key, safe="")}/{product}/{bbox}/{days}/{day.isoformat()}'
            r = client.get(url); r.raise_for_status()
            frames.append(_read_csv(r.text)); day += timedelta(days=days)
        return pd.concat(frames, ignore_index=True)


def _product(product, hours, now, key):
    token = (product, hours, bool(key))
    entry = _cache.get(token)
    path = CACHE_DIR / f'{product}_{hours}_{"api" if key else "public"}.json'
    if entry is None and path.exists():
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            entry = (datetime.fromisoformat(saved['fetched_at']), _read_csv(saved['csv']))
            _cache[token] = entry
        except Exception:
            entry = None
    age = (now-entry[0]).total_seconds() if entry else float('inf')
    if entry and 0 <= age < TTL:
        return entry[1], entry[0], False, None
    # Brief failure cooldown prevents refresh clicks from hammering an unavailable product.
    recent_failure = _failed.get(token)
    if recent_failure and 0 <= (now-recent_failure).total_seconds() < 60:
        error = 'Source temporarily unavailable; retrying shortly.'
    else:
        try:
            frame = _download(product, hours, now, key)
            _cache[token] = (now, frame); _failed.pop(token, None)
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix('.'+uuid4().hex+'.tmp')
                temp.write_text(json.dumps({'fetched_at': now.isoformat(), 'csv': frame.to_csv(index=False)}), encoding='utf-8')
                temp.replace(path)
            except OSError:
                pass  # a read-only cache directory must not hide a successful live response
            return frame, now, False, None
        except Exception:
            _failed[token] = now
            error = 'Source could not be refreshed. NASA may have a delay, outage or product change.'
    if entry and 0 <= age <= 86400:
        return entry[1], entry[0], True, error
    return None, None, True, error


@lru_cache(maxsize=1)
def australia_geometry():
    world = json.loads((ROOT/'frontend/public/maps/world.geojson').read_text(encoding='utf-8'))
    feature = next(f for f in world['features'] if f['properties'].get('ADMIN') == 'Australia')
    # Natural Earth is generalized; a small coastal tolerance avoids a hard low-resolution edge.
    return shape(feature['geometry']).buffer(.03)


def _normalise(frame, product):
    frame = frame.copy()
    frame['lat'] = pd.to_numeric(frame['latitude'], errors='coerce')
    frame['lon'] = pd.to_numeric(frame['longitude'], errors='coerce')
    raw_time = frame['acq_time'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(4)
    valid_time = raw_time.str.fullmatch(r'(?:[01]\d|2[0-3])[0-5]\d').fillna(False)
    frame['timestamp'] = pd.to_datetime(frame['acq_date'].astype(str)+' '+raw_time, format='%Y-%m-%d %H%M', errors='coerce', utc=True)
    frame = frame.loc[valid_time & frame['timestamp'].notna() & frame['lat'].between(-44.5, -9) & frame['lon'].between(112, 154.5)].copy()
    frame['time'] = raw_time.loc[frame.index]
    sensor, satellite, _, _ = PRODUCTS[product]
    frame['sensor'] = sensor
    native_sat = frame.get('satellite', pd.Series(satellite, index=frame.index)).astype(str).str.strip()
    frame['satellite'] = native_sat.replace({'T':'Terra','A':'Aqua'}) if sensor == 'MODIS' else satellite
    confidence = frame['confidence'].astype(str).str.strip().str.lower()
    number = pd.to_numeric(confidence, errors='coerce')
    frame['sensor_confidence'] = frame['confidence'].astype(str)
    if sensor == 'MODIS':
        frame['confidence'] = np.where(number>=80, 'high', np.where(number>=30, 'nominal', 'low'))
        good = number.between(30,100)
    else:
        frame['confidence'] = confidence.map({'l':'low','n':'nominal','h':'high'}).fillna('unknown')
        good = confidence.isin(['n','h'])
    types = pd.to_numeric(frame.get('type', pd.Series(-1,index=frame.index)), errors='coerce').fillna(-1)
    frame['quality_pass'] = good & types.isin([0,-1])
    frame['type'] = types
    frame['frp'] = pd.to_numeric(frame.get('frp',pd.Series(np.nan,index=frame.index)),errors='coerce')
    frame.loc[~np.isfinite(frame['frp']) | (frame['frp']<0),'frp'] = np.nan
    frame['daynight'] = frame.get('daynight',pd.Series('',index=frame.index)).astype(str)
    frame['product'] = product
    # FIRMS products have different optional/raw columns. Combine only the live
    # schema with explicit types, so empty responses and entirely missing FRP
    # cannot change pandas' inferred concatenation types.
    columns = ['lat', 'lon', 'timestamp', 'time', 'sensor', 'satellite',
               'confidence', 'sensor_confidence', 'quality_pass', 'type', 'frp',
               'daynight', 'product']
    types = {c: 'object' for c in columns if c not in
             ('lat', 'lon', 'timestamp', 'quality_pass', 'type', 'frp')}
    types.update(lat='float64', lon='float64', type='float64',
                 frp='float64', quality_pass='bool')
    return frame[columns].astype(types)


def snapshot(q):
    validate_australia(q.aoi)  # invalid areas never initiate NASA requests
    now = utcnow(); cutoff = now-timedelta(hours=q.hours)
    load_dotenv(ROOT/'.env',override=False);load_dotenv(ROOT/'backend/.env',override=False)
    key = os.getenv('FIRMS_MAP_KEY','').strip()
    if key == 'replace_with_your_private_firms_map_key': key = ''
    with _lock:
        with ThreadPoolExecutor(max_workers=4) as pool:
            products=list(PRODUCTS)
            responses=list(pool.map(lambda product:_product(product,q.hours,now,key), products))
    frames=[];sources=[]
    for product,(frame,fetched,stale,error) in zip(products,responses):
        source={'product':product,'fetched_at':fetched.isoformat() if fetched else None,'status':'stale' if frame is not None and stale else 'unavailable' if frame is None else 'current','warning':error}
        if frame is not None:
            try: frames.append(_normalise(frame,product))
            except Exception:
                source.update(status='unavailable',warning='Source records could not be validated.')
        sources.append(source)
    if not frames:
        raise HTTPException(503,'No NASA FIRMS sources are available right now. Retry shortly. No saved archive has been substituted for the live feed.')
    populated = [frame for frame in frames if not frame.empty]
    df = pd.concat(populated, ignore_index=True) if populated else frames[0].iloc[:0].copy()
    df=df.loc[(df['timestamp']>=cutoff)&(df['timestamp']<=now)]
    if q.quality_only: df=df.loc[df['quality_pass']]
    if not df.empty:
        mask=shapely.intersects_xy(australia_geometry(),df['lon'].to_numpy(),df['lat'].to_numpy())
        df=df.loc[mask]
        if q.aoi.type != 'region':
            mask=shapely.intersects_xy(aoi_geometry(q.aoi),df['lon'].to_numpy(),df['lat'].to_numpy())
            df=df.loc[mask]
    df=df.drop_duplicates(['satellite','lat','lon','timestamp']).sort_values('timestamp',ascending=False)
    fields=['lat','lon','satellite','sensor','frp','confidence','sensor_confidence','daynight','type','product']
    points=[]
    for row in df.to_dict('records'):
        point={k:(None if pd.isna(row[k]) else row[k]) for k in fields}
        timestamp=row['timestamp'];point.update(date=timestamp.strftime('%Y-%m-%d'),time=timestamp.strftime('%H%M'),timestamp=timestamp.isoformat(),age_hours=round((now-timestamp).total_seconds()/3600,2))
        points.append(point)
    available=[s for s in sources if s['status']!='unavailable']
    metadata={'count':len(points),'today_count':sum(p['date']==now.date().isoformat() for p in points),
        'latest_observation':points[0]['timestamp'] if points else None,'as_of':now.isoformat(),
        'window_start':cutoff.isoformat(),'window_end':now.isoformat(),'hours':q.hours,
        'fetched_at':min((s['fetched_at'] for s in available if s['fetched_at']),default=None),
        'sources':sources,'partial':any(s['status']!='current' for s in sources),
        'stale':not any(s['status']=='current' for s in sources),'refresh_seconds':TTL,
        'source':'NASA FIRMS Area API' if key else 'NASA FIRMS public regional feeds',
        'quality_only':q.quality_only,'boundary':'Natural Earth Australia boundary with approximately 3 km coastal tolerance',
        'note':'Near-real-time satellite detections, not a continuous fire watch. Today is incomplete. Multiple satellites can observe the same fire; these counts are not unique fire events. NRT fire type may be unknown. Viewing coverage is unknown.'}
    return metadata,points


@router.post('/hotspots')
def live_hotspots(q: LiveQuery):
    meta,points=snapshot(q)
    return {**meta,'points':points[:q.limit],'returned':min(len(points),q.limit),'truncated':len(points)>q.limit}


@router.post('/export.csv')
def export_live(q: LiveQuery):
    meta,points=snapshot(q)
    fields=['lat','lon','date','time','timestamp','sensor','satellite','frp','confidence','sensor_confidence','daynight','type','product','feed_as_of_utc','window_start_utc','window_end_utc','partial_feed','stale_feed']
    def rows():
        buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=fields)
        writer.writeheader();yield buf.getvalue()
        for point in points:
            buf.seek(0);buf.truncate()
            row={k:point.get(k) for k in fields};row.update(feed_as_of_utc=meta['as_of'],window_start_utc=meta['window_start'],window_end_utc=meta['window_end'],partial_feed=meta['partial'],stale_feed=meta['stale'])
            writer.writerow(row);yield buf.getvalue()
    return StreamingResponse(rows(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="embercal-recent-hotspots.csv"'})

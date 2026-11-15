"""Australia is the only supported prototype extent (not a country boundary mask)."""
from fastapi import HTTPException
from .aoi import aoi_geometry

def validate_australia(aoi):
    if aoi.type == 'region':
        if aoi.region != 'Australia':
            raise HTTPException(422, 'Only Australia is supported by this prototype.')
        return
    try:
        geometry = aoi_geometry(aoi)
        if geometry.is_empty:
            raise ValueError('empty geometry')
        w, s, e, n = geometry.bounds
    except Exception as exc:
        raise HTTPException(422, 'Provide a valid, non-empty selection geometry.') from exc
    if not (112 <= w < e <= 154.5 and -44.5 <= s < n <= -9):
        raise HTTPException(422, 'Select an area inside the Australia study extent only.')

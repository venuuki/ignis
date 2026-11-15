"""Optional national engine. Saved study screens work before analysis dependencies/data exist."""
from importlib.util import find_spec
from pathlib import Path
from fastapi import APIRouter, HTTPException

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix='/api/analysis', tags=['Australia analysis setup'])
REQUIRED = ('monthly_cells.parquet', 'monthly_meta.parquet', 'cells.parquet', 'harmonization_report.json')
MODULES = ('polars', 'duckdb', 'shapely', 'scipy', 'pydantic_settings', 'cachetools')

@router.get('/readiness')
def readiness():
    missing_dependencies = [name for name in MODULES if find_spec(name) is None]
    missing_data = [name for name in REQUIRED if not (ROOT / 'data' / 'processed' / name).is_file()]
    return {
        'ready': not missing_dependencies and not missing_data,
        'region': 'Australia', 'extent': [112, -44.5, 154.5, -9],
        'missing_dependencies': missing_dependencies, 'missing_data': missing_data,
        'forecast_ready': all((ROOT / 'data' / 'processed' / name).is_file() for name in ('forecasts.parquet', 'forecast_report.json')),
        'setup': '.\\Setup.ps1', 'build': '.\\Build-Australia.ps1',
        'note': 'National outputs are not included in the source ZIP. Existing saved study results remain available.',
    }

if not readiness()['missing_dependencies']:
    from embercal_engine.main import app as engine
    # The engine has its own /api/analysis prefix.
    root_router = APIRouter()
    root_router.include_router(router)
    root_router.include_router(engine)
    router = root_router
else:
    @router.api_route('/{path:path}', methods=['GET', 'POST'])
    def unavailable(path: str):
        raise HTTPException(503, 'Install Australia analysis dependencies with Setup.ps1, then restart the backend.')

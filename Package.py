"""Create a portable source/data/built-frontend archive without private/runtime files."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib

ROOT = Path(__file__).resolve().parent
EXCLUDED = {'.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__', '.pytest_cache',
            '.mypy_cache', '.ruff_cache', 'regional_cache', '.cache', '.codex', '.agents',
            '.aws', '.ssh', '.vscode', '.claude', 'coverage', 'test-results', 'playwright-report'}
def include(path):
    parts = path.relative_to(ROOT).parts
    if path.is_symlink() or any(p.lower() in EXCLUDED or p.endswith('.egg-info') for p in parts):
        return False
    name = path.name.lower()
    return not (name.startswith('.env') or name.endswith(('.pyc','.pyo','.zip','.log','.bak','.tmp','.tsbuildinfo','.pem','.key'))
                or any(word in name for word in ('api_key','apikey','credentials','secret')))

if __name__ == '__main__':
    output = ROOT.parent / 'IGNIS-Revamp.zip'
    paths = sorted(p for p in ROOT.rglob('*') if p.is_file() and include(p))
    with ZipFile(output, 'w', ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            archive.write(path, Path('IGNIS-Revamp') / path.relative_to(ROOT))
    print(f'{output} ({len(paths)} files, {output.stat().st_size:,} bytes)')
    print('SHA256:', hashlib.sha256(output.read_bytes()).hexdigest())

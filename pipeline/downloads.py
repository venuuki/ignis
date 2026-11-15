"""Verified streaming downloads for large public FIRMS archives."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests


def download_archive(url: str, destination: Path, session, *, attempts: int = 5, refresh: bool = False) -> int:
    """Publish only complete files. Retain partial bytes for validated range retries.

    Returns an HTTP status; exhausted transient failures raise an actionable error.
    If a server ignores Range or changes its validator, a 200 response restarts safely.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    metadata = destination.with_name(destination.name + '.part.json')
    if refresh:
        partial.write_bytes(b'')
        metadata.write_text('{}', encoding='utf-8')
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            state = json.loads(metadata.read_text(encoding='utf-8')) if metadata.exists() else {}
        except (ValueError, OSError):
            state = {}
        validator = state.get('etag') or state.get('last_modified')
        offset = partial.stat().st_size if partial.exists() and state.get('url') == url and validator else 0
        headers = {'Accept-Encoding': 'identity'}
        if offset:
            headers.update({'Range': f'bytes={offset}-', 'If-Range': validator})
        print(f'  {destination.name}: attempt {attempt}/{attempts}' + (f', resume at {offset / 1e6:.1f} MB' if offset else ''), flush=True)
        try:
            with session.get(url, headers=headers, stream=True, timeout=(30, 120)) as response:
                status = response.status_code
                if status == 416:
                    # A stale or already-complete partial must be revalidated by a fresh GET.
                    metadata.write_text('{}', encoding='utf-8')
                    raise requests.RequestException('Server rejected the saved byte range; restarting')
                if status in (408, 429) or 500 <= status <= 599:
                    response.raise_for_status()
                if status not in (200, 206):
                    return status
                if response.headers.get('Content-Encoding', 'identity').lower() not in ('identity', ''):
                    raise requests.RequestException('Unexpected encoded response; byte-length verification is unavailable')
                length = response.headers.get('Content-Length')
                expected_chunk = int(length) if length is not None else None
                if status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if not offset or not match or int(match[1]) != offset:
                        metadata.write_text('{}', encoding='utf-8')
                        raise requests.RequestException('Invalid resume range; restarting')
                    begin, end, total = map(int, match.groups())
                    response_validator = response.headers.get('ETag') or response.headers.get('Last-Modified')
                    if end < begin or end >= total or (state.get('total') is not None and state['total'] != total) or (response_validator and response_validator != validator):
                        metadata.write_text('{}', encoding='utf-8')
                        raise requests.RequestException('Archive changed during resume; restarting')
                    if expected_chunk is not None and expected_chunk != end - begin + 1:
                        metadata.write_text('{}', encoding='utf-8')
                        raise requests.RequestException('Inconsistent response byte counts; restarting')
                    expected_chunk = end - begin + 1
                    mode = 'ab'
                else:
                    offset, total, mode = 0, expected_chunk, 'wb'
                state = dict(url=url, total=total,
                             etag=response.headers.get('ETag'), last_modified=response.headers.get('Last-Modified'))
                # Write metadata after truncating a restarted partial, so interrupted restarts stay consistent.
                with partial.open(mode) as output:
                    metadata.write_text(json.dumps(state), encoding='utf-8')
                    received, reported = 0, offset
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        output.write(chunk)
                        received += len(chunk)
                        if offset + received - reported >= 25_000_000:
                            reported = offset + received
                            print(f'    {destination.name}: {reported / 1e6:.1f}' + (f' / {total / 1e6:.1f}' if total else '') + ' MB', flush=True)
                if expected_chunk is not None and received != expected_chunk:
                    raise requests.RequestException(f'Incomplete response: {received} of {expected_chunk} bytes')
                if total is not None and partial.stat().st_size != total:
                    raise requests.RequestException('Archive is still incomplete; retrying remaining bytes')
                if partial.stat().st_size == 0:
                    raise requests.RequestException('Empty archive response')
                partial.replace(destination)
                metadata.unlink(missing_ok=True)
                return 200
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                delay = min(2 ** attempt, 16)
                print(f'  Transfer interrupted for {destination.name}; retrying in {delay}s.', flush=True)
                time.sleep(delay)
    raise RuntimeError(f'Could not finish {destination.name} after {attempts} attempts. '
                       'Rerun the ingest step; completed Parquet files are reused and validated partial downloads are retained.') from last_error

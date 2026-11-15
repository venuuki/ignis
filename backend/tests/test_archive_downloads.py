import json
from pathlib import Path

import pytest
import requests

from pipeline import downloads


class Response:
    def __init__(self, chunks=(), status=200, **headers):
        self.status_code = status
        self.headers = headers
        self.chunks = chunks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(kwargs)
        assert kwargs['stream'] is True
        return next(self.responses)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(downloads.time, 'sleep', lambda _: None)


def test_interrupted_archive_resumes_and_only_publishes_complete_file(tmp_path):
    path = tmp_path / 'archive.csv'
    first = Response([b'abc', requests.exceptions.ChunkedEncodingError('cut off')], **{'Content-Length': '6', 'ETag': '"v1"'})
    second = Response([b'def'], status=206, **{'Content-Length': '3', 'Content-Range': 'bytes 3-5/6', 'ETag': '"v1"'})
    session = Session([first, second])
    assert downloads.download_archive('https://example.test/archive', path, session) == 200
    assert path.read_bytes() == b'abcdef'
    assert session.calls[1]['headers']['Range'] == 'bytes=3-'
    assert session.calls[1]['headers']['If-Range'] == '"v1"'
    assert first.closed and second.closed
    assert not path.with_name(path.name + '.part').exists()


def test_server_ignoring_resume_restarts_instead_of_appending(tmp_path):
    path = tmp_path / 'archive.csv'
    session = Session([
        Response([b'abc', requests.ConnectionError('cut off')], **{'Content-Length': '6', 'ETag': '"v1"'}),
        Response([b'ABCDEF'], **{'Content-Length': '6', 'ETag': '"v2"'}),
    ])
    downloads.download_archive('https://example.test/archive', path, session)
    assert path.read_bytes() == b'ABCDEF'


def test_short_response_is_not_published_and_can_resume_on_later_run(tmp_path):
    path = tmp_path / 'archive.csv'
    session = Session([Response([b'abc'], **{'Content-Length': '6', 'ETag': '"v1"'})])
    with pytest.raises(RuntimeError, match='Rerun the ingest step'):
        downloads.download_archive('https://example.test/archive', path, session, attempts=1)
    assert not path.exists()
    resumed = Session([Response([b'def'], status=206, **{'Content-Range': 'bytes 3-5/6', 'ETag': '"v1"'})])
    downloads.download_archive('https://example.test/archive', path, resumed)
    assert resumed.calls[0]['headers']['Range'] == 'bytes=3-'
    assert path.read_bytes() == b'abcdef'


def test_invalid_range_cannot_corrupt_download(tmp_path):
    path = tmp_path / 'archive.csv'
    session = Session([
        Response([b'abc', requests.ConnectionError('cut off')], **{'Content-Length': '6', 'ETag': '"v1"'}),
        Response([b'wrong'], status=206, **{'Content-Range': 'bytes 2-5/6', 'ETag': '"v1"'}),
        Response([b'abcdef'], **{'Content-Length': '6', 'ETag': '"v1"'}),
    ])
    downloads.download_archive('https://example.test/archive', path, session)
    assert 'Range' not in session.calls[2]['headers']
    assert path.read_bytes() == b'abcdef'


def test_transient_server_error_retries_but_missing_archive_does_not(tmp_path):
    path = tmp_path / 'archive.csv'
    session = Session([Response(status=503), Response([b'abc'], **{'Content-Length': '3'})])
    assert downloads.download_archive('https://example.test/archive', path, session) == 200
    assert len(session.calls) == 2
    missing = Session([Response(status=404)])
    assert downloads.download_archive('https://example.test/missing', tmp_path / 'missing.csv', missing) == 404
    assert not (tmp_path / 'missing.csv').exists()


def test_completed_parquet_is_reused_without_network(monkeypatch, tmp_path):
    import polars as pl
    from pipeline import ingest_firms
    monkeypatch.setattr(ingest_firms, 'HOT_DIR', tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('Completed files must be reused')
    monkeypatch.setattr(ingest_firms, 'download_archive', forbidden)
    pl.DataFrame({'sample': [1, 2]}).write_parquet(tmp_path / 'Australia__modis__2020.parquet')
    assert ingest_firms.archive_file('modis', 2020, 'Australia', {}, False) == ('modis', 2020, 'Australia', 2)

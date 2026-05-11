"""Tests for EPFR HTTP client functions."""

# ruff: noqa: D102

from dataclasses import replace
import importlib
from pathlib import Path
from typing import Any, cast

from workflows.epfr import client
from workflows.epfr.client import _get_unp, build_download_url, build_page_url
from workflows.epfr.config import EPFR_DEFAULTS
from workflows.epfr.models import EpfrApiResponse, EpfrRecord, Holder


aiohttp = importlib.import_module("aiohttp")
pytest = importlib.import_module("pytest")


@pytest.fixture()
def anyio_backend():
    return "asyncio"


class FakeResponse:
    def __init__(self, status=200, json_data=None, text_data="", chunks=None):
        self.status = status
        self._json_data = json_data
        self._text_data = text_data
        self.content = FakeStream(chunks or [])

    async def json(self):
        return self._json_data

    async def text(self):
        return self._text_data


class FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def iter_chunked(self, _chunk_size):
        for chunk in self._chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class FakeRequestContext:
    def __init__(self, result):
        self._result = result

    async def __aenter__(self):
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


class FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def get(self, url, *, timeout):
        self.calls.append({"url": url, "timeout": timeout})
        if not self._results:
            raise AssertionError("No fake HTTP results left")
        return FakeRequestContext(self._results.pop(0))


class FakeClientSession:
    def __init__(self, *args, **kwargs):
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


class TestBuildPageUrl:
    """Tests for build_page_url function."""

    def test_page_zero(self):
        url = build_page_url(0, "2026-03-01")
        assert "pageNo=0" in url
        assert "searchDateFrom=2026-03-01" in url
        assert "search=%D0%B4%D0%B8%D0%B2%D0%B8%D0%B4%D0%B5%D0%BD%D0%B4" in url
        assert "sortField=realUploadDate" in url
        assert "sortDir=desc" in url
        assert "subCategoryId=1" in url

    def test_page_ten(self):
        url = build_page_url(10, "2026-01-01")
        assert "pageNo=10" in url
        assert "searchDateFrom=2026-01-01" in url

    def test_different_dates(self):
        url1 = build_page_url(0, "2025-06-15")
        url2 = build_page_url(0, "2026-12-31")
        assert url1 != url2
        assert "2025-06-15" in url1
        assert "2026-12-31" in url2

    def test_with_date_to(self):
        url = build_page_url(0, "2026-03-01", date_to="2026-06-30")
        assert "searchDateFrom=2026-03-01" in url
        assert "searchDateTo=2026-06-30" in url

    def test_without_date_to(self):
        url = build_page_url(0, "2026-03-01")
        assert "searchDateFrom=2026-03-01" in url
        assert "searchDateTo" not in url

    def test_empty_date_to_omitted(self):
        url = build_page_url(0, "2026-03-01", date_to="")
        assert "searchDateTo" not in url

    def test_both_dates_in_url(self):
        url = build_page_url(5, "2025-01-01", date_to="2026-12-31")
        assert "pageNo=5" in url
        assert "searchDateFrom=2025-01-01" in url
        assert "searchDateTo=2026-12-31" in url


class TestBuildDownloadUrl:
    """Tests for build_download_url function."""

    def test_simple_id(self):
        url = build_download_url(141278)
        assert url == "https://epfr.gov.by/portal/file/141278/content"

    def test_different_ids(self):
        url1 = build_download_url(1)
        url2 = build_download_url(99999)
        assert "1/content" in url1
        assert "99999/content" in url2


class TestGetUnp:
    """Tests for _get_unp helper function."""

    def _make_record(
        self,
        holder_unp: str = "",
        holder_title: str = "",
    ) -> EpfrRecord:
        holder = Holder(id=1, title=holder_title, unp=holder_unp) if holder_unp or holder_title else None
        return EpfrRecord(id=1, name="Test", holder=holder)

    def test_holder_unp_preferred(self):
        rec = self._make_record(holder_unp="123")
        assert _get_unp(rec) == "123"

    def test_unknown_when_no_unp(self):
        rec = self._make_record()
        assert _get_unp(rec) == "unknown"

    def test_holder_unp_with_value(self):
        rec = self._make_record(holder_unp="600073968")
        assert _get_unp(rec) == "600073968"


@pytest.mark.anyio
class TestFetchPage:
    async def test_success_parses_fixture_payload(self, load_epfr_fixture_json):
        payload = load_epfr_fixture_json("epfr_api_response_null_subcategory.json")
        session = FakeSession([FakeResponse(status=200, json_data=payload)])

        response = await client.fetch_page(cast(Any, session), 0, "2026-03-01")

        assert isinstance(response, EpfrApiResponse)
        assert response.total_pages == 257
        assert response.last is False
        assert len(response.content) == 14
        assert response.content[0].id == 137086
        assert response.content[-1].holder is not None
        assert response.content[-1].holder.unp == "200166738"
        assert "pageNo=0" in session.calls[0]["url"]

    async def test_http_500_retries_then_succeeds(self, monkeypatch, load_epfr_fixture_json):
        payload = load_epfr_fixture_json("epfr_api_response_null_subcategory.json")
        session = FakeSession(
            [
                FakeResponse(status=500, text_data="server error"),
                FakeResponse(status=200, json_data=payload),
            ]
        )
        sleeps = []
        monkeypatch.setattr(client, "load_epfr_config", lambda: replace(EPFR_DEFAULTS, max_retries=3))

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(client.asyncio, "sleep", fake_sleep)

        response = await client.fetch_page(cast(Any, session), 2, "2026-03-01")

        assert response.content[0].holder is not None
        assert response.content[0].holder.unp == "700049607"
        assert len(session.calls) == 2
        assert sleeps == [2]

    @pytest.mark.parametrize(
        "exc_factory",
        [
            lambda: TimeoutError("timed out"),
            lambda: aiohttp.ClientError("boom"),
        ],
    )
    async def test_transient_exceptions_retry_and_raise_runtime_error(
        self,
        monkeypatch,
        exc_factory,
    ):
        session = FakeSession([exc_factory(), exc_factory(), exc_factory()])
        sleeps = []
        monkeypatch.setattr(client, "load_epfr_config", lambda: replace(EPFR_DEFAULTS, max_retries=3))

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(client.asyncio, "sleep", fake_sleep)

        with pytest.raises(RuntimeError, match=r"Page 4: all 3 attempts failed"):
            await client.fetch_page(cast(Any, session), 4, "2026-03-01")

        assert len(session.calls) == 3
        assert sleeps == [2, 4]


@pytest.mark.anyio
class TestDownloadFile:
    async def test_download_writes_temp_file_then_detected_extension(self, monkeypatch, tmp_path):
        company_dir = tmp_path / "700049607"
        session = FakeSession(
            [
                FakeResponse(
                    status=200,
                    chunks=[b"%PDF-1.7\n", b"body"],
                )
            ]
        )
        monkeypatch.setattr(
            client, "load_epfr_config", lambda: replace(EPFR_DEFAULTS, download_retries=1, chunk_size=4)
        )

        result = await client.download_file(
            cast(Any, session), 137086, company_dir, semaphore=client.asyncio.Semaphore(1)
        )

        assert result == (137086, True, None, "137086.pdf")
        saved_path = company_dir / "137086.pdf"
        assert saved_path.exists()
        assert saved_path.read_bytes() == b"%PDF-1.7\nbody"
        assert not list(company_dir.glob(".download_*.tmp"))

    async def test_failed_download_cleans_up_partial_temp_file(self, monkeypatch, tmp_path):
        company_dir = tmp_path / "500196148"
        session = FakeSession(
            [
                FakeResponse(
                    status=200,
                    chunks=[b"%PDF-1.7\n", aiohttp.ClientError("stream broke")],
                )
            ]
        )
        sleeps = []
        monkeypatch.setattr(
            client, "load_epfr_config", lambda: replace(EPFR_DEFAULTS, download_retries=1, chunk_size=4)
        )

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(client.asyncio, "sleep", fake_sleep)

        result = await client.download_file(
            cast(Any, session), 136958, company_dir, semaphore=client.asyncio.Semaphore(1)
        )

        assert result == (136958, False, "Failed after 1 attempts", "")
        assert not (company_dir / "136958.pdf").exists()
        assert not list(company_dir.glob(".download_*.tmp"))
        assert sleeps == []


@pytest.mark.anyio
class TestDownloadAllFiles:
    async def test_groups_records_and_reports_stats(self, monkeypatch, tmp_path, load_epfr_fixture_json):
        payload = load_epfr_fixture_json("epfr_api_response_null_subcategory.json")
        records = EpfrApiResponse.model_validate(payload).content
        grouped_records = [
            records[0],
            records[0].model_copy(update={"id": 137999, "name": f"{records[0].name} copy"}),
            records[1],
        ]
        calls = []

        async def fake_download_file(session, record_id, company_dir, semaphore):
            del session, semaphore
            calls.append((record_id, Path(company_dir)))
            if record_id == 136958:
                return (record_id, False, "HTTP 500: server error", "")
            return (record_id, True, None, f"{record_id}.pdf")

        monkeypatch.setattr(client, "download_file", fake_download_file)
        monkeypatch.setattr(client.aiohttp, "ClientSession", FakeClientSession)
        monkeypatch.setattr(client, "load_epfr_config", lambda: replace(EPFR_DEFAULTS, max_concurrent_downloads=2))

        stats = await client.download_all_files(grouped_records, tmp_path)

        assert stats == {
            "total_records": 3,
            "total_files_attempted": 3,
            "successful": 2,
            "failed": 1,
            "failed_ids": [136958],
            "file_map": {137086: "137086.pdf", 137999: "137999.pdf"},
            "by_unp": {
                "700049607": {
                    "success": 2,
                    "failed": 0,
                    "files": [
                        {"id": 137086, "filename": "137086.pdf"},
                        {"id": 137999, "filename": "137999.pdf"},
                    ],
                },
                "500196148": {
                    "success": 0,
                    "failed": 1,
                    "files": [],
                },
            },
        }
        assert calls == [
            (137086, tmp_path / "700049607"),
            (137999, tmp_path / "700049607"),
            (136958, tmp_path / "500196148"),
        ]

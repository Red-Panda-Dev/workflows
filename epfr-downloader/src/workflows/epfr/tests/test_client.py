"""Tests for EPFR HTTP client functions."""

# ruff: noqa: D102

from ..client import _get_unp, build_download_url, build_page_url
from ..models import EpfrRecord, Organization


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
        org_unp: str = "",
        holder_title: str = "",
        org_title: str = "",
    ) -> EpfrRecord:
        holder = Organization(id=1, title=holder_title, unp=holder_unp) if holder_unp or holder_title else None
        org = Organization(id=2, title=org_title, unp=org_unp) if org_unp or org_title else None
        return EpfrRecord(id=1, name="Test", holder=holder, organization=org)

    def test_holder_unp_preferred(self):
        rec = self._make_record(holder_unp="123", org_unp="456")
        assert _get_unp(rec) == "123"

    def test_fallback_to_org_unp(self):
        rec = self._make_record(org_unp="456")
        assert _get_unp(rec) == "456"

    def test_unknown_when_no_unp(self):
        rec = self._make_record()
        assert _get_unp(rec) == "unknown"

    def test_holder_empty_unp_falls_back(self):
        rec = self._make_record(holder_unp="", org_unp="789")
        assert _get_unp(rec) == "789"

    def test_holder_unp_with_value(self):
        rec = self._make_record(holder_unp="600073968")
        assert _get_unp(rec) == "600073968"

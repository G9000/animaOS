from __future__ import annotations

import pytest
from anima_server.services.documents import parsing_pack


@pytest.fixture(autouse=True)
def reset_pack_state(tmp_path, monkeypatch):
    monkeypatch.setattr(parsing_pack, "_marker_path", lambda: tmp_path / "parsing-pack.ready")
    parsing_pack._reset_state_for_tests()


def test_status_absent_when_docling_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(parsing_pack, "_docling_installed", lambda: False)

    status = parsing_pack.pack_status()

    assert status.state == "absent"
    assert not parsing_pack.parsing_pack_ready()


def test_status_ready_when_marker_present(monkeypatch) -> None:
    monkeypatch.setattr(parsing_pack, "_docling_installed", lambda: True)
    parsing_pack._marker_path().write_text("1")

    assert parsing_pack.pack_status().state == "ready"
    assert parsing_pack.parsing_pack_ready()


def test_ensure_prefetches_models_and_marks_ready(monkeypatch) -> None:
    monkeypatch.setattr(parsing_pack, "_docling_installed", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(parsing_pack, "_prefetch_models", lambda: calls.append("fetched"))

    status = parsing_pack.ensure_parsing_pack()
    parsing_pack._wait_for_download_for_tests(timeout=5)

    assert status.state in {"downloading", "ready"}
    assert calls == ["fetched"]
    assert parsing_pack.pack_status().state == "ready"


def test_prefetch_failure_reports_error(monkeypatch) -> None:
    monkeypatch.setattr(parsing_pack, "_docling_installed", lambda: True)

    def boom() -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(parsing_pack, "_prefetch_models", boom)

    parsing_pack.ensure_parsing_pack()
    parsing_pack._wait_for_download_for_tests(timeout=5)

    status = parsing_pack.pack_status()
    assert status.state == "error"
    assert "network down" in (status.error or "")

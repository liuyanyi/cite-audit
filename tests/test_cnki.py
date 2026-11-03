import pytest

from cite_audit.cnki import _candidate
from cite_audit.settings import DEFAULTS
from cite_audit.sources import SourceError, SourceRegistry


def test_cnki_bib_is_candidate_with_original_export():
    bib = "@article{cn, title={中文题名}, author={张三 and 李四}, year={2024}, doi={10.1000/test}}"
    record = _candidate("CJFQ!test", "https://kns.cnki.net/detail/test", bib)
    assert record["raw_payload"]["response_body"] == bib
    assert record["status"] == "candidate"
    assert record["fields"]["title"] == "中文题名"
    assert record["identifiers"] == {"doi": "10.1000/test"}


@pytest.mark.parametrize("payload", ["验证码", "@article{a,title={甲}}\n@article{b,title={乙}}"])
def test_cnki_rejects_unusable_export(payload):
    with pytest.raises(SourceError):
        _candidate("id", "https://kns.cnki.net/detail/test", payload)


async def test_cnki_opt_in_for_existing_settings(monkeypatch):
    assert DEFAULTS["source_enabled"]["cnki"] is False
    enabled = dict.fromkeys(DEFAULTS["source_enabled"], False)
    del enabled["cnki"]
    async def unexpected(*args):
        pytest.fail("disabled provider was invoked")
    monkeypatch.setattr(SourceRegistry, "_cnki", unexpected)
    async def audit(event):
        pass
    assert await SourceRegistry().lookup({"title": "中文题名"}, "online", {"source_enabled": enabled},
                                         audit=audit, cancelled=lambda: False) == []


async def test_manual_verification_preserves_session_past_request_deadline(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from cite_audit.cnki import SEARCH, _wait_for

    frame = SimpleNamespace(url="https://kns.cnki.net/verify/home")
    page = Mock(frames=[frame], bring_to_front=AsyncMock(), is_closed=lambda: False)
    page.get_by_text.return_value.first.is_visible = AsyncMock(return_value=False)
    expected = Mock(is_visible=AsyncMock(return_value=True))
    session = SimpleNamespace(timeout=0, event=AsyncMock())
    async def solve(_delay):
        frame.url = SEARCH
    monkeypatch.setattr(asyncio, "sleep", solve)
    await _wait_for(page, expected, session)
    page.bring_to_front.assert_awaited_once()
    assert [call.args[1] for call in session.event.await_args_list] == ["等待人工验证", "人工验证"]


async def test_closed_challenge_ends_lookup():
    from unittest.mock import Mock

    from cite_audit.cnki import _manual_verification

    with pytest.raises(SourceError, match="知网窗口已关闭"):
        await _manual_verification(Mock(is_closed=lambda: True), None)


async def test_queries_share_live_page_until_closed():
    from unittest.mock import AsyncMock, Mock

    from cite_audit.cnki import BrowserSession

    browser = Mock(is_connected=lambda: True, close=AsyncMock())
    pages = [Mock(is_closed=lambda: False, context=Mock(route=AsyncMock())) for _ in range(2)]
    context = Mock(new_page=AsyncMock(side_effect=pages), route=AsyncMock())
    browser.new_context = AsyncMock(return_value=context)
    driver = Mock(stop=AsyncMock(), chromium=Mock(launch=AsyncMock(return_value=browser)))
    playwright = Mock(return_value=Mock(start=AsyncMock(return_value=driver)))
    shared = BrowserSession()
    assert await shared.get_page(playwright) is pages[0]
    assert await shared.get_page(playwright) is pages[0]
    pages[0].is_closed = lambda: True
    assert await shared.get_page(playwright) is pages[1]
    driver.chromium.launch.assert_awaited_once()
    await shared.close()
    driver.stop.assert_awaited_once()
    assert shared.page is None


@pytest.mark.parametrize(("url", "redirect"), [
    ("http://kns.cnki.net/kns8s/search?captchaId=test%2Bid", True),
    ("http://kns.cnki.net/dm8/manage/export.html?captchaId=test", True),
    ("http://kns.cnki.net/other?captchaId=test", False),
    ("http://kns.cnki.net.example.org/kns8s/search?captchaId=test", False),
])
async def test_cnki_captcha_return_upgrades_only_search_navigation(url, redirect):
    from unittest.mock import AsyncMock, Mock

    from cite_audit.cnki import route_request

    request = Mock(url=url, method="GET", is_navigation_request=lambda: True)
    route = Mock(request=request, fulfill=AsyncMock(), abort=AsyncMock(), continue_=AsyncMock())
    await route_request(route)
    if redirect:
        assert route.fulfill.await_args.kwargs["headers"]["Location"] == url.replace("http:", "https:", 1)
        assert route.fulfill.await_args.kwargs["status"] == 307
    else:
        route.abort.assert_awaited_once()
    route.continue_.assert_not_awaited()

def test_batch_exports_follow_file_ids_even_for_reordered_identical_titles():
    from cite_audit.cnki import EXPORT, _batch_candidates

    links = {"db!A!1!0": "url-a", "db!B!1!0": "url-b"}
    entries = ["@article{ B, title={同名}, year={2020}}", "@article{ A, title={同名}, year={2021}}"]
    records = _batch_candidates(links, entries, EXPORT)
    assert [(r["source_url"], r["fields"]["year"]) for r in records] == [("url-b", "2020"), ("url-a", "2021")]
    with pytest.raises(SourceError):
        _batch_candidates(links, entries[:1], EXPORT)
    with pytest.raises(SourceError):
        _batch_candidates(links, entries[:1] * 2, EXPORT)


async def test_task_combines_titles_and_exports_once(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from cite_audit import cnki
    from cite_audit.policy import PROVIDERS
    from cite_audit.service import AuditService

    pytest.importorskip("playwright.async_api")
    monkeypatch.setattr(cnki.random, "uniform", lambda *_: 0)
    searched = []
    exported = []
    monkeypatch.setattr(cnki.BrowserSession, "get_page", AsyncMock(return_value=object()))

    async def search(page, title, session):
        await cnki._charge(session, "普通检索")
        searched.append(title)
        return {title: "https://kns.cnki.net/" + title}, {title: title}

    async def export(page, links, titles, session, *, partial):
        await cnki._charge(session, "导出 BibTeX")
        exported.append(list(links))
        return [cnki._candidate(key, links[key], "@article{x,title={" + key + "}}") for key in reversed(links)]

    monkeypatch.setattr(cnki, "_search_title", search)
    monkeypatch.setattr(cnki, "_export", export)
    service = AuditService(tmp_path)
    service.settings.update({"source_enabled": {p: p == "cnki" for p in PROVIDERS}})
    task = service.create_task("@misc{a,title={{中文 {Alpha}}}}\n@misc{b,title={中文 {Beta}}}\n@misc{c,title={中文 Alpha}}\n@misc{d,title={English only},author={张三}}".encode(),
                               "test.bib", "", "online", {"request_budget": 3, "entry_request_budget": 2},
                               confirm_budget=True)
    await service.run_task(task["id"])
    assert searched == ["中文 Alpha", "中文 Beta"] and exported == [["中文 Alpha", "中文 Beta"]]
    assert sum(c["request_count"] for c in service.calls(task["id"])) == 3
    assert all(e["execution_status"] == "completed" for e in service.entries(task["id"], True))


def test_cnki_cache_survives_restart_and_has_no_expiry_and_respects_refresh(tmp_path):
    from types import SimpleNamespace

    from cite_audit.cnki import BrowserSession
    from cite_audit.storage import Store

    db = tmp_path / "audit.db"
    session = SimpleNamespace(limit=2, settings={})
    records = [_candidate("id", "url", "@article{x,title={原始题录}}")]
    BrowserSession(Store(db)).remember("标题", session, records)
    restarted = BrowserSession(Store(db))
    assert restarted.cached("标题", session) == records
    session.settings["force_refresh"] = True
    assert restarted.cached("标题", session) is None
    session.settings.clear()
    restarted.store.execute("UPDATE cnki_query_cache SET saved_at=saved_at-?", (365 * 86400,))
    assert restarted.cached("标题", session) == records
    assert restarted.clear_cache() == 1
    assert restarted.cached("标题", session) is None


async def test_cnki_search_interval_starts_after_previous_search(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from cite_audit import cnki

    shared = cnki.BrowserSession()
    clock = Mock(monotonic=Mock(return_value=100))
    monkeypatch.setattr(cnki, "time", clock)
    monkeypatch.setattr(cnki.random, "uniform", Mock(return_value=33))
    monkeypatch.setattr(cnki, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    monkeypatch.setattr(cnki, "_search_title", AsyncMock())
    session = SimpleNamespace(event=AsyncMock())
    await shared.search_title(None, "first", session)
    cnki.asyncio.sleep.assert_not_awaited()
    clock.monotonic.return_value = 105
    await shared.search_title(None, "next", session)
    cnki.asyncio.sleep.assert_awaited_once_with(28)
    cnki.random.uniform.assert_called_with(20, 45)


def test_cnki_journal_identity_without_year(tmp_path):
    from cite_audit.assessment import current_match
    from cite_audit.knowledge import Knowledge
    from cite_audit.storage import Store

    bib = "@article{ABCD20260921002,title={某方法},author={李辰宇 and 陶九阳},journal={指挥控制与仿真},issn={1673-3819},pages={1-9}}"
    record = _candidate("row-token", "https://kns.cnki.net/kcms2/article/abstract", bib)
    knowledge = Knowledge(Store(tmp_path / "audit.db"))
    source, = knowledge.ingest([record], "online")
    version = knowledge.version(source["version_id"])
    fields = dict(record["fields"])
    assert current_match(fields, version)[0]
    for changes in ({"author": "李辰宇"}, {"journal": "其他期刊"}, {"pages": "1-10"},
                    {"year": "2024"}, {"issn": ""}, {"doi": "10.1000/other"}):
        assert not current_match({**fields, **changes}, version)[0]

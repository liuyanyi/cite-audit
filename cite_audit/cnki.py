"""CNKI ordinary title search and single-record browser-session export."""
import asyncio
import random
import re
import sys
import time
from copy import deepcopy
from urllib.parse import parse_qs, urlencode, urlsplit

from .bib import MAX_BYTES, parse_bib
from .policy import cnki_search_title
from .sources import (
    BudgetExceeded,
    LookupCancelled,
    Response,
    SourceError,
    _interruptible,
    _record,
    _snapshot,
    normalize_doi,
    now,
)
from .storage import dump, load

ORIGIN = "https://kns.cnki.net"
SEARCH = ORIGIN + "/kns8s/search"
EXPORT = ORIGIN + "/dm8/manage/export.html"


async def _charge(session, operation):
    if session.remaining <= 0:
        raise BudgetExceeded("知网检索请求预算已用完")
    session.remaining -= 1
    await session.event("cnki", operation, "started", request_count=1)


def _candidate(identifier, url, bib, *, export_url=EXPORT):
    if not bib or len(bib.encode()) > MAX_BYTES:
        raise SourceError("知网未返回有效的单条 BibTeX 题录")
    document = parse_bib(bib)
    if len(document.entries) != 1 or document.diagnostics:
        raise SourceError("知网导出内容不是完整的单条 BibTeX")
    entry = document.entries[0]
    if not entry.fields.get("title") or entry.diagnostics:
        raise SourceError("知网导出的题录缺少题名或无法解析")
    response = Response(200, {"content-type": "application/x-bibtex"}, bib.encode(), export_url, now())
    return _record("cnki", identifier, url, "aggregator", {**entry.fields, "entrytype": entry.entry_type},
                   {"doi": normalize_doi(entry.fields.get("doi", ""))}, _snapshot(response))


def _batch_candidates(links, citations, export_url, titles=None, *, partial=False):
    if len(links) == len(citations) == 1:
        identifier, url = next(iter(links.items()))
        return [_candidate(identifier, url, citations[0], export_url=export_url)]
    # CNKI may sort the export independently of search rank. Bind by its file ID.
    remaining = dict(links)
    result = []
    for bib in citations:
        document = parse_bib(bib)
        if len(document.entries) != 1:
            if partial:
                continue
            raise SourceError("知网批量题录无法解析")
        entry = document.entries[0]
        key = entry.key.strip().casefold()
        matches = []
        for identifier, url in links.items():
            parts = identifier.split("!")
            file_ids = [v for k, values in parse_qs(urlsplit(url).query).items()
                        if k.lower() == "filename" for v in values]
            if len(parts) > 1:
                file_ids.append(parts[1])
            if any(value.casefold() == key for value in file_ids):
                matches.append(identifier)
        if not matches and titles:
            title = "".join(entry.fields.get("title", "").split())
            matches = [identifier for identifier in links if titles[identifier] == title and title]
        if len(matches) != 1 or matches[0] not in remaining:
            if partial:
                continue
            raise SourceError("知网批量题录无法唯一对应搜索结果")
        identifier = matches[0]
        result.append(_candidate(identifier, remaining.pop(identifier), bib, export_url=export_url))
    if remaining and not partial:
        raise SourceError("知网批量导出缺少部分题录")
    return result


def _verification_url(url):
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc == "kns.cnki.net" and parts.path.startswith("/verify/")


async def _challenge_visible(page):
    if page.is_closed():
        raise SourceError("知网窗口已关闭，已结束本次知网查询")
    if any(_verification_url(frame.url) for frame in page.frames):
        return True
    return await page.get_by_text(re.compile(r"请完成安全验证|拖动滑块完成验证")).first.is_visible()


async def _manual_verification(page, session):
    if not await _challenge_visible(page):
        return False
    await page.bring_to_front()
    await session.event("cnki", "等待人工验证", "started",
                        message="请在服务主机的知网窗口完成验证，随后自动继续。也可关闭窗口结束本次查询。")
    while await _challenge_visible(page):
        await asyncio.sleep(0.25)
    await session.event("cnki", "人工验证", "success", message="验证页面已退出，正在恢复查询")
    return True


async def _wait_for(page, locator, session):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + session.timeout
    while True:
        if await _manual_verification(page, session):
            deadline = loop.time() + session.timeout
        if await locator.is_visible():
            return
        if loop.time() >= deadline:
            raise SourceError("知网未显示预期的检索页面")
        await asyncio.sleep(0.2)


async def route_request(route):
    url = urlsplit(route.request.url)
    # CNKI may send the verified browser back to its search page with an HTTP URL.
    if (url.scheme == "http" and url.netloc == "kns.cnki.net" and url.path in {"/kns8s/search", "/dm8/manage/export.html"}
            and route.request.method == "GET" and route.request.is_navigation_request()):
        await route.fulfill(status=307, headers={"Location": url._replace(scheme="https").geturl(),
                                                "Cache-Control": "no-store"})
        return
    if (url.scheme == "https" and (url.hostname or "").endswith(".cnki.net")
            and url.port in {None, 443}):
        await route.continue_()
    else:
        await route.abort()


class BrowserSession:
    def __init__(self, store=None):
        self.store = store
        self.next_search = 0.0
        self.lock = asyncio.Lock()
        self.cache = {}
        self.driver = None
        self.browser = None
        self.context = None
        self.page = None

    def cached(self, title, session):
        if session.settings.get("force_refresh") or not session.settings.get("source_cache_ttl_seconds", 3600):
            return None
        key = (title, session.limit)
        if self.store is not None:
            row = self.store.one("SELECT records FROM cnki_query_cache WHERE title=? AND candidate_limit=?", key)
            return load(row["records"]) if row else None
        return deepcopy(self.cache.get(key))

    def remember(self, title, session, records):
        if not records or not session.settings.get("source_cache_ttl_seconds", 3600):
            return
        key = (title, session.limit)
        if self.store is not None:
            self.store.execute("INSERT OR REPLACE INTO cnki_query_cache VALUES(?,?,?,?)",
                               (*key, time.time(), dump(records)))
        else:
            if len(self.cache) >= 32:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = deepcopy(records)

    def clear_cache(self):
        if self.store is not None:
            return self.store.execute("DELETE FROM cnki_query_cache")
        count = len(self.cache)
        self.cache.clear()
        return count

    async def search_title(self, page, title, session):
        delay = self.next_search - time.monotonic()
        if delay > 0:
            await session.event("cnki", "检索间隔", "started", message=f"下一次普通检索前等待 {delay:.0f} 秒")
            await asyncio.sleep(delay)
        try:
            return await _search_title(page, title, session)
        finally:
            self.next_search = time.monotonic() + random.uniform(20, 45)

    async def get_page(self, playwright):
        if self.browser is None or not self.browser.is_connected():
            await self.close()
            self.driver = await playwright().start()
            try:
                self.browser = await self.driver.chromium.launch(
                    headless=False, channel="msedge" if sys.platform == "win32" else None)
            except BaseException:
                await self.close()
                raise
        if self.page is None or self.page.is_closed():
            if self.context is None:
                self.context = await self.browser.new_context(service_workers="block", accept_downloads=False)
                await self.context.route("**/*", route_request)
            self.page = await self.context.new_page()
        return self.page

    async def close(self):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            self.page = self.context = self.browser = None
            if self.driver:
                await self.driver.stop()
                self.driver = None


async def _search_title(page, title, session):
    page.set_default_timeout(session.timeout * 1000)
    await _charge(session, "普通检索")
    await page.goto(SEARCH, wait_until="domcontentloaded")
    query = page.locator("input.search-input:visible").first
    await _wait_for(page, query, session)
    await query.fill(title)
    await page.locator("input.search-btn:visible").first.click()
    rows = page.locator("tr").filter(has=page.locator("input.cbItem"))
    empty = page.get_by_text(re.compile(r"未检索到相关|未找到相关|共找到\s*0\s*条"))
    await _wait_for(page, rows.first.or_(empty.first).first, session)
    if not await rows.count():
        return {}, {}
    links = {}
    titles = {}
    for row in (await rows.all())[:session.limit]:
        identifier = await row.locator("input.cbItem").input_value()
        anchor = row.locator("a.fz14, td.name a").first
        url = await anchor.evaluate("a => a.href")
        titles[identifier] = "".join((await anchor.inner_text()).split())
        if not identifier or not url.startswith(ORIGIN + "/"):
            raise SourceError("知网结果缺少可核对的导出标识或详情链接")
        links[identifier] = url
    return links, titles


async def _export(page, group, titles, session, *, partial=False):
    await _charge(session, "导出 BibTeX")
    export_url = EXPORT + "?" + urlencode({"filename": ",".join(group), "displaymode": "BibTex",
                                           "uniplatform": "NZKPT"})
    exported = await page.context.new_page()
    try:
        await exported.goto(export_url, wait_until="domcontentloaded", timeout=session.timeout * 1000)
        citation = exported.locator("li").filter(has_text=re.compile(r"^\s*@[a-zA-Z]+\s*\{"))
        try:
            await _wait_for(exported, citation.nth(len(group) - 1), session)
        except SourceError:
            if not partial or exported.is_closed() or not await citation.count():
                raise
        citations = [text.strip() for text in await citation.all_inner_texts()]
        return _batch_candidates(group, citations, export_url, titles, partial=partial)
    finally:
        await exported.close()


async def lookup(fields, session, shared):
    title = cnki_search_title(fields.get("title", ""))
    if not title:
        return []
    try:
        from playwright.async_api import Error, async_playwright
    except ImportError:
        raise SourceError("知网需要可选依赖，请安装 cnki extra") from None

    async def search():
        async with shared.lock:
            hit = shared.cached(title, session)
            if hit is not None:
                await session.event("cnki", "复用检索及导出结果", "success", cache_hit=True)
                return hit
            page = await shared.get_page(async_playwright)
            links, titles = await shared.search_title(page, title, session)
            if not links:
                return []
            # Opaque CNKI IDs are common. Keep same-title candidates in separate exports.
            groups = []
            for identifier in links:
                for group in groups:
                    if all(titles[other] != titles[identifier] for other in group):
                        group[identifier] = links[identifier]
                        break
                else:
                    groups.append({identifier: links[identifier]})
            async def export(group):
                session.partial.extend(await _export(page, group, titles, session))
            for group in groups:
                try:
                    await export(group)
                except (BudgetExceeded, LookupCancelled):
                    raise
                except SourceError:
                    if len(group) == 1:
                        raise
                    await session.event("cnki", "批量导出", "started", message="批量结果不完整，按剩余预算逐条导出")
                    for identifier, url in group.items():
                        await export({identifier: url})
            shared.remember(title, session, session.partial)
            return session.partial
    try:
        return await _interruptible(search(), session.cancelled, float("inf"))
    except (Error, TimeoutError):
        raise SourceError("知网浏览器检索或导出失败，请检查浏览器安装、网络、访问验证及页面结构") from None

async def task_lookup(titles, make_session, shared, cancelled):
    """One search per distinct uncached title and at most one export for the revision."""
    results = {}
    pending = {}
    links = {}
    labels = {}
    try:
        from playwright.async_api import Error, async_playwright
    except ImportError:
        for title in titles:
            await make_session(title).event("cnki", "普通检索", "error", error="知网需要可选依赖 cnki")
        return results

    async def collect():
        async with shared.lock:
            page = None
            for title in titles:
                session = make_session(title)
                hit = shared.cached(title, session)
                if hit is not None:
                    results[title] = hit
                    await session.event("cnki", "复用检索及导出结果", "success", cache_hit=True)
                    continue
                if session.remaining < 2:
                    await session.event("cnki", "普通检索", "budget_exhausted", error="请求预算不足以检索并导出")
                    continue
                try:
                    if page is None:
                        page = await shared.get_page(async_playwright)
                    found, names = await shared.search_title(page, title, session)
                except (Error, SourceError) as exc:
                    await session.event("cnki", "普通检索", "error", error=str(exc) if isinstance(exc, SourceError)
                                        else "知网普通检索失败")
                    continue
                if found:
                    pending[title] = list(found)
                    links.update(found)
                    labels.update(names)
                else:
                    await session.event("cnki", "普通检索", "empty")
            if not pending:
                return
            owner = next(iter(pending))
            exported = await _export(page, links, labels, make_session(owner), partial=True)
            by_id = {r["record_id"]: r for r in exported}
            for title, ids in pending.items():
                records = [by_id[i] for i in ids if i in by_id]
                session = make_session(title)
                results[title] = records
                if len(records) != len(ids):
                    await session.event("cnki", "任务批量导出结果", "partial",
                                        error="部分题录缺失或无法唯一对应搜索结果", record_count=len(records))
                    continue
                shared.remember(title, session, records)
                await session.event("cnki", "任务批量导出结果", "success", record_count=len(records))

    try:
        await _interruptible(collect(), cancelled, float("inf"))
    except LookupCancelled:
        pass
    except (Error, SourceError, TimeoutError) as exc:
        for title in titles:
            if title not in results:
                await make_session(title).event("cnki", "任务批量导出", "error",
                                                error=str(exc) if isinstance(exc, SourceError) else "知网页面处理失败")
    return results

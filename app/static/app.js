/* cite-audit: rendering is text-only. Uploaded text is never interpreted as HTML. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = {tasks: [], task: null, selected: null, tab: 'comparison', query: '', filter: 'all', taskStamp: '', settings: null, loading: 0};
  const runningStates = new Set(['queued', 'pending', 'running', 'processing', 'querying', 'cancelling']);
  const statusLabels = {queued: '排队中', pending: '排队中', running: '核验中', processing: '核验中', querying: '查询中', completed: '已完成', done: '已完成', complete: '已完成', partial: '部分完成', completed_with_errors: '部分完成', failed: '失败', error: '失败', cancelled: '已取消', canceled: '已取消', cancelling: '取消中', cached: '已复用'};
  const labels = {
    identity: {unique: '唯一确定', multiple: '多个候选', not_found: '未找到', insufficient: '证据不足'},
    field: {consistent: '一致', conflict: '存在冲突', incomplete: '可补全', partial: '部分未验证'},
    doi: {verified: '已核验', unverified: '未核验', missing: '缺失待查', conflict: '归属冲突', not_found: '未找到', failed: '查询失败'},
    publication: {published: '已是正式版', candidate: '正式版候选', not_found: '未发现正式版', unclear: '关系不明'},
    judgment: {consistent: '一致', correction: '修正', completion: '补全', normalization: '规范化', upgrade: '版本升级', unverified: '未核验', not_applicable: '不适用'}
  };
  let toastTimer, pollTimer, pollBusy = false, confirming = null, reconnectAt = 0;
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false && !key.startsWith('aria-')) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
      else if (key === 'value') node.value = value;
      else if (key === 'checked') node.checked = !!value;
      else if (key === 'disabled') node.disabled = !!value;
      else node.setAttribute(key, key.startsWith('aria-') ? String(value) : value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child !== undefined && child !== null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon'); svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#i-' + name); svg.append(use); return svg;
  }
  function button(text, action, cls = '', iconName = '') {
    return el('button', {type: 'button', class: 'button ' + cls, onclick: action}, iconName && icon(iconName), text);
  }
  function badge(text, tone = 'neutral') { return el('span', {class: 'badge ' + tone}, text); }
  function str(value) { if (value === null || value === undefined || value === '') return ''; if (typeof value === 'object') return JSON.stringify(value, null, 2); return String(value); }
  function safeURL(value) { try { const u = new URL(String(value)); return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : null; } catch { return null; } }
  function link(text, url, cls = '') { const href = safeURL(url); return href ? el('a', {href, target: '_blank', rel: 'noopener noreferrer', class: cls}, text, icon('link')) : el('span', {class: 'subtle'}, text); }
  function date(value, withTime = false) {
    if (!value) return '时间未知';
    const d = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN', withTime ? {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'} : {month: '2-digit', day: '2-digit'});
  }
  function taskMode(task = state.task) { return task?.mode === 'live' ? 'live' : 'mock'; }
  function isRunning(task = state.task) { return !!task && runningStates.has(task.status); }
  function entryResult(entry) { return entry.result || {}; }
  function entries() { return Array.isArray(state.task?.entries) ? state.task.entries : []; }
  function selectedEntry() { return entries().find(e => String(e.id) === String(state.selected)); }
  function issueText(issue) { return typeof issue === 'string' ? issue : issue?.message || issue?.reason || issue?.code || str(issue); }
  function toast(message, error = false) {
    clearTimeout(toastTimer); $('toast').textContent = message; $('toast').className = 'toast' + (error ? ' error' : ''); $('toast').hidden = false;
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, error ? 6500 : 3200);
  }
  async function api(path, options = {}) {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), options.timeout || 90000);
    try {
      const headers = {...options.headers};
      if (options.body && !(options.body instanceof FormData)) { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(options.body); }
      const response = await fetch(path, {...options, headers, signal: controller.signal, cache: 'no-store'});
      const text = await response.text(); let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = {message: text.slice(0, 300)}; }
      if (!response.ok) {
        const detail = data.detail || data.error || data.message;
        throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(issueText).join('；') : '请求失败（' + response.status + '）');
      }
      return data;
    } catch (error) { if (error.name === 'AbortError') throw new Error('请求超时，操作结果尚不确定。请刷新后确认，勿重复提交。'); throw error; }
    finally { clearTimeout(timer); }
  }
  async function copy(text) {
    if (!text) return;
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(String(text));
      else { const area = el('textarea', {value: String(text), 'aria-label': '复制内容'}); area.style.position = 'fixed'; area.style.opacity = '0'; document.body.append(area); area.select(); const ok = document.execCommand('copy'); area.remove(); if (!ok) throw new Error(); }
      toast('已复制');
    } catch { toast('复制失败，请选中文本后手动复制。', true); }
  }
  function dialog(id) { const d = $(id); if (!d.open) d.showModal(); }
  function closeDialog(id) { $(id).close(); }
  function confirmAction(title, description, label = '确认', options = []) {
    if (confirming) return Promise.resolve(false);
    $('confirm-title').textContent = title; $('confirm-description').textContent = description; $('confirm-submit').textContent = label;
    $('confirm-options').replaceChildren(...options); dialog('confirm-dialog');
    return new Promise(resolve => { confirming = resolve; });
  }
  $('confirm-form').addEventListener('submit', e => { e.preventDefault(); if (confirming) { const done = confirming; confirming = null; closeDialog('confirm-dialog'); done(true); } });
  $('confirm-dialog').addEventListener('close', () => { if (confirming) { const done = confirming; confirming = null; done(false); } });
  document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => closeDialog(b.dataset.close)));
  document.querySelectorAll('dialog').forEach(d => d.addEventListener('click', e => { if (e.target === d) { const r = d.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) d.close(); } }));

  function renderHistory() {
    $('task-count').textContent = state.tasks.length;
    $('task-list').replaceChildren(...(state.tasks.length ? state.tasks.map(task => el('button', {
      type: 'button', class: 'history-item' + (String(task.id) === String(state.task?.id) ? ' active' : ''), 'aria-current': String(task.id) === String(state.task?.id) ? 'true' : undefined,
      onclick: () => loadTask(task.id, true), title: task.filename || '未命名文件'
    }, el('span', {class: 'history-title'}, icon('document'), el('span', {}, task.filename || '未命名文件')),
    el('span', {class: 'history-meta'}, el('span', {}, date(task.created_at), task.mode === 'mock' ? ' · 演示' : ''), el('span', {class: 'history-status' + (isRunning(task) ? ' running' : '')}, statusLabels[task.status] || task.status)))) : [el('p', {class: 'sidebar-empty'}, '暂无检查记录')]));
  }
  async function refreshHistory() {
    const data = await api('/api/tasks'); state.tasks = data.tasks || []; renderHistory(); return state.tasks;
  }
  async function loadTask(id, reset = false, silent = false) {
    const sequence = ++state.loading;
    try {
      const task = await api('/api/tasks/' + encodeURIComponent(id));
      if (sequence !== state.loading) return;
      const changed = JSON.stringify(task) !== state.taskStamp;
      const previous = selectedEntry();
      state.task = task;
      if (reset || !entries().some(e => String(e.id) === String(state.selected))) {
        state.selected = (reset ? null : entries().find(e => previous && e.fingerprint === previous.fingerprint))?.id || entries()[0]?.id || null;
        if (reset) { state.tab = 'comparison'; state.query = ''; state.filter = 'all'; $('entry-search').value = ''; $('entry-filter').value = 'all'; }
      }
      try { localStorage.setItem('cite-audit.task', String(task.id)); } catch { /* Storage can be disabled. */ }
      const historyIndex = state.tasks.findIndex(t => String(t.id) === String(task.id));
      if (historyIndex >= 0) state.tasks[historyIndex] = task; else state.tasks.unshift(task);
      if (changed || reset) { renderTask(); renderHistory(); state.taskStamp = JSON.stringify(task); }
      $('connection-error').hidden = true;
    } catch (error) { if (!silent) toast(error.message, true); $('connection-error').textContent = '无法更新任务：' + error.message; $('connection-error').hidden = false; }
  }
  function renderTask() {
    const task = state.task; if (!task) return;
    $('welcome').hidden = true; $('task-workspace').hidden = false; $('task-title').textContent = task.filename || '文献检查';
    $('task-subtitle').replaceChildren(el('span', {}, date(task.created_at, true)), el('span', {}, statusLabels[task.status] || task.status), task.parent_id ? el('span', {}, '增量复查') : null);
    $('task-actions').replaceChildren(
      el('a', {class: 'button', href: '/api/tasks/' + encodeURIComponent(task.id) + '/source', download: ''}, icon('download'), '原文件'),
      el('a', {class: 'button', href: '/api/tasks/' + encodeURIComponent(task.id) + '/report', download: ''}, icon('document'), '检查报告'),
      isRunning(task) ? button('取消', cancelTask, 'quiet') : button('复查', openUpload, '', 'upload'),
      !isRunning(task) ? button('刷新过期', refreshStale, 'quiet', 'refresh') : null,
      Number(task.failed) > 0 && !isRunning(task) ? button('重试失败', retryTask, 'quiet') : null,
      !isRunning(task) ? button('删除', deleteTask, 'quiet danger-text') : null
    );
    $('mode-banner').replaceChildren(...(taskMode(task) === 'mock' ? [el('div', {class: 'banner demo'}, icon('alert'), el('span', {}, el('strong', {}, '离线演示'), ' · 所有模拟证据均非真实检索结果，请勿据此修改文献。'))] : []));
    const total = Number(task.total) || entries().length; const completed = Number(task.completed) || 0;
    const metrics = [['进度', [String(completed), el('small', {}, ' / ' + total)], ''], ['问题', task.issues || 0, 'warning'], ['失败', task.failed || 0, task.failed ? 'danger' : 'muted'], ['缓存命中', task.cache_hits || 0, 'muted'], ['API 调用', task.api_calls || 0, 'muted'], ['文献', total, 'muted']];
    $('task-metrics').replaceChildren(...metrics.map(([label, value, cls]) => el('div', {class: 'metric ' + cls}, el('span', {class: 'metric-label'}, label), el('span', {class: 'metric-value'}, value))));
    const progress = total ? Math.min(100, Math.round(completed / total * 100)) : 0; $('progress-fill').style.width = progress + '%'; $('progress-track').setAttribute('aria-valuenow', String(progress));
    const parseIssues = task.parse_issues || [];
    $('parse-issues').replaceChildren(...(parseIssues.length ? [el('details', {class: 'parse-issues'}, el('summary', {}, '解析提示 · ' + parseIssues.length), el('ul', {}, parseIssues.map(i => el('li', {}, issueText(i)))))] : []));
    renderEntries(); renderDetail();
  }
  function entryHasIssues(entry) { const r = entryResult(entry); return (entry.issues?.length || r.issues?.length || ['conflict', 'incomplete', 'partial'].includes(r.field_state) || ['conflict', 'failed', 'unverified'].includes(r.doi_state) || ['multiple', 'insufficient', 'not_found'].includes(r.identity_state) || entry.status === 'failed'); }
  function matches(entry) {
    const r = entryResult(entry); const f = entry.fields || {};
    if (state.query && !(String(entry.citation_key || '') + ' ' + str(f.title)).toLowerCase().includes(state.query)) return false;
    switch (state.filter) {
      case 'issues': return entryHasIssues(entry);
      case 'conflict': return r.field_state === 'conflict' || r.doi_state === 'conflict';
      case 'missing_doi': return !f.doi || ['missing', 'not_found', 'unverified'].includes(r.doi_state);
      case 'upgrade': return r.publication_state === 'candidate';
      case 'insufficient': return ['insufficient', 'multiple', 'not_found'].includes(r.identity_state) || r.field_state === 'partial' || r.doi_state === 'unverified';
      case 'failed': return ['failed', 'error'].includes(entry.status) || r.doi_state === 'failed';
      case 'chinese': return !!entry.is_chinese;
      default: return true;
    }
  }
  function entryBadges(entry) {
    const r = entryResult(entry), flags = [];
    if (['failed', 'error'].includes(entry.status)) return [badge('查询失败', 'danger')];
    if (!entry.result) return [badge(statusLabels[entry.status] || '待核验', isRunning(entry) ? 'primary' : 'neutral')];
    if (r.doi_state === 'conflict') flags.push(badge('DOI 冲突', 'danger'));
    else if (r.field_state === 'conflict') flags.push(badge('字段冲突', 'warning'));
    if (r.publication_state === 'candidate') flags.push(badge('正式版候选', 'primary'));
    if (['insufficient', 'multiple', 'not_found'].includes(r.identity_state)) flags.push(badge(labels.identity[r.identity_state]));
    else if (r.field_state === 'partial') flags.push(badge('部分未核验'));
    if (['missing', 'not_found', 'unverified'].includes(r.doi_state)) flags.push(badge('DOI ' + labels.doi[r.doi_state]));
    if (r.field_state === 'incomplete') flags.push(badge('可补全', 'warning'));
    if (!flags.length) flags.push(badge(r.field_state === 'consistent' ? '字段一致' : '查看结果', r.field_state === 'consistent' ? 'good' : 'neutral'));
    if (entry.reused || entry.cache_hit || r.cache_hit) flags.push(badge('缓存'));
    return flags.slice(0, 3);
  }
  function renderEntries() {
    const filtered = entries().filter(matches); $('entry-count').textContent = filtered.length + ' 条';
    const list = $('entries'), scrollTop = list.scrollTop, scrollLeft = list.scrollLeft;
    list.replaceChildren(...(filtered.length ? filtered.map(entry => el('div', {role: 'listitem'}, el('button', {type: 'button', class: 'entry-item' + (String(state.selected) === String(entry.id) ? ' selected' : ''), 'aria-pressed': String(state.selected) === String(entry.id), onclick: () => { state.selected = entry.id; state.tab = 'comparison'; renderEntries(); renderDetail(); $('entry-detail').scrollTop = 0; }},
      el('span', {class: 'entry-key-row'}, el('span', {class: 'entry-key'}, entry.citation_key || '(无 key)'), el('span', {class: 'entry-year'}, entry.fields?.year || '—')),
      el('span', {class: 'entry-title'}, entry.fields?.title || '缺少标题'), el('span', {class: 'entry-flags'}, entryBadges(entry))))) : [el('div', {class: 'empty-detail'}, state.query || state.filter !== 'all' ? '没有匹配的文献' : '没有可解析的文献')]));
    list.scrollTop = scrollTop; list.scrollLeft = scrollLeft;
  }
  function stateBadge(kind, value) {
    const tone = ['unique', 'consistent', 'verified', 'published'].includes(value) ? 'good' : value === 'conflict' ? 'danger' : ['candidate', 'incomplete'].includes(value) ? 'primary' : 'neutral';
    return badge(labels[kind][value] || '待核验', tone);
  }
  function renderDetail() {
    const panel = $('entry-detail'), scrollTop = panel.scrollTop, entry = selectedEntry();
    if (!entry) { panel.replaceChildren(el('div', {class: 'empty-detail'}, '选择一条文献查看证据')); return; }
    const result = entryResult(entry), evidence = Array.isArray(result.evidence) ? result.evidence : Array.isArray(result.evidences) ? result.evidences : [];
    const tabs = [['comparison', '字段对照'], ['evidence', '证据', evidence.length], ['original', '原始条目'], ['queries', '查询记录']];
    const header = el('header', {class: 'detail-header'},
      el('div', {class: 'detail-overline'}, el('span', {class: 'detail-key'}, '@' + (entry.entry_type || 'misc') + ' · ' + (entry.citation_key || '无 key')),
        el('div', {class: 'detail-tools'}, button('补充题录', openManual, 'small', 'plus'), button('刷新条目', refreshEntry, 'small', 'refresh'))),
      el('h2', {class: 'detail-title'}, entry.fields?.title || '缺少标题'),
      el('p', {class: 'detail-authors'}, entry.fields?.author || '作者未提供'),
      el('div', {class: 'state-grid'}, [['identity', '身份'], ['field', '字段'], ['doi', 'DOI'], ['publication', '版本']].map(([kind, label]) => el('div', {class: 'state-item'}, el('span', {}, label), stateBadge(kind, result[kind + '_state']))))
    );
    const tablist = el('div', {class: 'detail-tabs', role: 'tablist', 'aria-label': '详情视图'}, tabs.map(([id, title, count]) => el('button', {type: 'button', role: 'tab', id: 'tab-' + id, 'aria-selected': state.tab === id, 'aria-controls': 'detail-tabpanel', tabindex: state.tab === id ? '0' : '-1', class: 'detail-tab' + (state.tab === id ? ' active' : ''), onclick: () => { state.tab = id; renderDetail(); $('tab-' + id).focus(); }, onkeydown: e => { if (['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) { e.preventDefault(); const index = tabs.findIndex(t => t[0] === state.tab); state.tab = tabs[e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (index + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length][0]; renderDetail(); $('tab-' + state.tab).focus(); } }}, title, count === undefined ? null : el('span', {}, count))));
    const body = el('div', {class: 'detail-body', id: 'detail-tabpanel', role: 'tabpanel', 'aria-labelledby': 'tab-' + state.tab});
    if (entry.error) body.append(el('div', {class: 'banner error'}, issueText(entry.error)));
    if (state.tab === 'comparison') renderComparison(body, entry, evidence);
    if (state.tab === 'evidence') renderEvidence(body, entry, evidence);
    if (state.tab === 'original') { body.append(el('div', {class: 'section-heading'}, el('h3', {}, '上传时的原始快照'), button('复制原条目', () => copy(entry.raw), 'small', 'copy')), el('pre', {class: 'code-box'}, entry.raw || '无原始文本')); }
    if (state.tab === 'queries') renderQueries(body, result);
    panel.replaceChildren(header, tablist, body); panel.scrollTop = scrollTop;
  }
  function evidenceRefs(ids, evidence) {
    return el('div', {class: 'field-evidence'}, (ids || []).map(id => {
      const index = evidence.findIndex(e => String(e.id) === String(id));
      return el('button', {type: 'button', class: 'evidence-ref', title: index < 0 ? '证据记录不可用' : (evidence[index].provider || '来源') + ' · ' + id, disabled: index < 0, onclick: () => { state.tab = 'evidence'; renderDetail(); const target = document.querySelector('[data-evidence-id="' + CSS.escape(String(id)) + '"]'); if (target) { target.classList.add('highlight'); target.scrollIntoView({behavior: 'smooth', block: 'nearest'}); } }}, index < 0 ? '证据缺失' : '[' + (index + 1) + ']');
    }));
  }
  function fieldValue(value, cls = '', missing = '未提供') {
    const text = str(value); return el('div', {class: 'value-row ' + cls}, el('span', {class: 'field-value' + (!text ? ' empty-value' : '')}, text || (value === '' ? '空值' : missing)), text ? el('button', {type: 'button', class: 'icon-button copy-value', 'aria-label': '复制字段值', title: '复制字段值', onclick: () => copy(text)}, icon('copy')) : null);
  }
  function renderComparison(body, entry, evidence) {
    const result = entryResult(entry); const assessments = Array.isArray(result.assessments) ? result.assessments : [];
    body.append(el('div', {class: 'section-heading'}, el('h3', {}, '当前版本'), result.suggested_bibtex ? button('复制建议 BibTeX', () => copy(result.suggested_bibtex), 'small', 'copy') : null));
    if (assessments.length) {
      body.append(el('table', {class: 'field-table'}, el('thead', {}, el('tr', {}, ['字段', '原值', '建议值', '判断 / 证据'].map(t => el('th', {scope: 'col'}, t)))),
        el('tbody', {}, assessments.filter(a => a.judgment !== 'upgrade').map(a => {
          const changed = ['correction', 'completion', 'normalization'].includes(a.judgment);
          const tone = a.judgment === 'consistent' ? 'good' : a.judgment === 'correction' ? 'warning' : ['completion', 'normalization'].includes(a.judgment) ? 'primary' : 'neutral';
          return el('tr', {}, el('td', {class: 'field-name'}, a.field), el('td', {}, fieldValue(a.original, 'old-value' + (changed ? ' changed' : ''))), el('td', {}, fieldValue(a.suggested, changed ? 'suggested-value' : '', '无建议')), el('td', {}, badge(labels.judgment[a.judgment] || a.judgment || '未核验', tone), evidenceRefs(a.evidence_ids, evidence), a.reason ? el('span', {class: 'assessment-reason'}, a.reason) : null));
        }))));
    } else body.append(el('p', {class: 'notice'}, isRunning(entry) || isRunning(state.task) && !entry.result ? '正在获取证据，字段结果将逐条更新。' : '尚无字段级核验结果。缺少证据不代表原值有误。'));
    const issues = [...(entry.issues || []), ...(result.issues || [])];
    if (issues.length) body.append(el('ul', {class: 'issues-list'}, [...new Set(issues.map(issueText))].map(i => el('li', {}, i))));
    if (entry.fields?.doi) { const doi = String(entry.fields.doi).replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, ''); body.append(el('div', {class: 'cnki-tools'}, link('原条目 DOI', 'https://doi.org/' + encodeURI(doi), 'button small'))); }
    const publication = result.publication_suggestion;
    if (publication || result.publication_state === 'candidate') {
      const section = el('section', {class: 'version-section'}, el('div', {class: 'section-heading'}, el('h3', {}, '正式发表版本'), publication?.suggested_bibtex ? button('复制正式版 BibTeX', () => copy(publication.suggested_bibtex), 'small', 'copy') : badge('保留当前版本', 'neutral')));
      if (publication) {
        section.append(el('p', {class: 'notice warning'}, publication.warning || '替换前请确认：正文是否依赖预印本特有内容。正式版建议与当前版本分开保存。'));
        const fields = publication.fields || {};
        section.append(el('dl', {class: 'source-fields'}, Object.entries(fields).map(([key, value]) => [el('dt', {}, key), el('dd', {}, fieldValue(value))])), evidenceRefs([...new Set([...(publication.evidence_ids || []), ...(publication.relation_evidence_ids || [])])], evidence));
        if (publication.original_arxiv_id) section.append(el('p', {class: 'source-meta'}, '原 arXiv：' + publication.original_arxiv_id + ' · 正式版类型：' + (publication.entry_type || '未提供')));
        if (publication.suggested_bibtex) section.append(el('details', {}, el('summary', {class: 'text-link'}, '查看正式版条目'), el('pre', {class: 'code-box'}, publication.suggested_bibtex)));
      } else section.append(el('p', {class: 'notice'}, '候选不等于可替换版本，仍需核对文献身份与版本关系。'));
      (Array.isArray(result.candidates) ? result.candidates : []).filter(candidate => candidate.version === 'published').forEach(candidate => {
        const f = candidate.fields || candidate;
        section.append(el('div', {class: 'candidate-card'}, el('p', {}, f.title || candidate.title || '候选记录'), el('div', {class: 'source-meta'}, [f.author, f.year, f.journal || f.booktitle, candidate.reason].filter(Boolean).map(str).join(' · ')), candidate.url ? link('查看候选来源', candidate.url, 'text-link') : null, evidenceRefs(candidate.evidence_ids || (candidate.id ? [candidate.id] : []), evidence)));
      }); body.append(section);
    }
    if (result.identity_state !== 'unique' && Array.isArray(result.candidates) && result.candidates.length) {
      body.append(el('section', {class: 'version-section'}, el('h3', {}, '文献候选 · 身份待核对'), result.candidates.map(candidate => { const f = candidate.fields || candidate; return el('div', {class: 'candidate-card'}, el('p', {}, f.title || '候选记录'), el('div', {class: 'source-meta'}, [f.author, f.year, f.journal || f.booktitle, candidate.provider].filter(Boolean).map(str).join(' · ')), evidenceRefs(candidate.evidence_ids || (candidate.id ? [candidate.id] : []), evidence)); })));
    }
    if (Array.isArray(result.model_suggestions) && result.model_suggestions.length) {
      body.append(el('section', {class: 'version-section'}, el('h3', {}, '模型证据线索 · 待人工核对'), el('p', {class: 'notice'}, '以下内容未作为已核验修正，也不自动写入建议条目。'), result.model_suggestions.map(item => el('div', {class: 'candidate-card'}, el('p', {}, str(item.field) + '：' + str(item.value)), item.reason ? el('div', {class: 'source-meta'}, item.reason) : null, item.quotation ? el('div', {class: 'source-meta'}, '来源摘录：' + str(item.quotation)) : null, evidenceRefs(item.evidence_ids, evidence)))));
    }
    if (result.suggested_bibtex) body.append(el('details', {class: 'version-section'}, el('summary', {class: 'text-link'}, '查看当前版本建议 BibTeX'), el('pre', {class: 'code-box'}, result.suggested_bibtex)));
  }
  function renderEvidence(body, entry, evidence) {
    if (entryResult(entry).expires_at) body.append(el('p', {class: 'source-meta evidence-check-meta'}, new Date(entryResult(entry).expires_at).getTime() < Date.now() ? '核验结果已过期，可刷新此条目。' : '核验结果有效至 ' + date(entryResult(entry).expires_at, true)));
    if (entryResult(entry).cache_source || entryResult(entry).checked_at) body.append(el('p', {class: 'source-meta evidence-check-meta'}, [entryResult(entry).checked_at ? '检查 ' + date(entryResult(entry).checked_at, true) : '', ({verification: '复用核验缓存', previous_evidence: '复用历史证据', manual: '人工补充', providers: '来源查询'})[entryResult(entry).cache_source] || ''].filter(Boolean).join(' · ')));
    if (!evidence.length) body.append(el('p', {class: 'notice'}, '暂无可展示的证据。未检索到不等于文献不存在。'));
    evidence.forEach((record, index) => {
      const f = record.fields || {}; const expired = record.expires_at && new Date(record.expires_at).getTime() < Date.now();
      const method = record.method || (taskMode() === 'mock' ? 'mock' : 'api'); const isLead = /lead|snippet|search_result/i.test(str(record.provenance));
      const card = el('article', {class: 'source-card', 'data-evidence-id': record.id},
        el('div', {class: 'source-top'}, el('div', {class: 'source-name'}, '[' + (index + 1) + '] ' + (record.provider || '来源'), badge(method === 'mock' ? '模拟证据' : method === 'manual' ? '人工 · 未在线核验' : isLead ? '检索线索 · 待核验' : '在线获取', method === 'mock' || method === 'manual' ? 'manual' : isLead ? 'warning' : 'good')), record.url ? link('来源', record.url, 'button small') : null),
        f.title ? el('p', {class: 'source-title'}, f.title) : null,
        el('div', {class: 'source-meta'}, '获取 ' + date(record.retrieved_at, true), record.version ? ' · 证据版本 ' + record.version : '', record.expires_at ? ' · ' + (expired ? '已过期' : '有效至 ' + date(record.expires_at, true)) : '', record.cache_hit || record.cached ? ' · 缓存' : ''),
        record.provenance ? el('div', {class: 'source-meta'}, '来源链：' + str(record.provenance)) : null,
        el('dl', {class: 'source-fields'}, Object.entries(f).filter(([key]) => key !== 'title').map(([key, value]) => [el('dt', {}, key), el('dd', {}, str(value) || '—')])),
        record.raw ? el('details', {}, el('summary', {}, '原始来源记录'), el('pre', {class: 'code-box'}, str(record.raw))) : null
      ); body.append(card);
    });
    const query = str(entry.fields?.title) + (entry.fields?.author ? ' ' + str(entry.fields.author).split(' and ')[0] : '') + ' site:cnki.net';
    body.append(el('section', {class: 'version-section'}, el('h3', {}, 'CNKI 公开网页补充'), el('div', {class: 'cnki-tools'}, link('打开 CNKI', 'https://www.cnki.net/', 'button small'), button('复制查询条件', () => copy(query), 'small', 'copy'), button('导入 / 粘贴题录', openManual, 'small', 'plus')), el('p', {class: 'notice'}, '公开网页检索不代表 CNKI 总库覆盖。题录或详情链接仅作为独立线索保留。')));
  }
  function tokenLabel(usage) {
    if (usage === null || usage === undefined) return '';
    let count = usage;
    if (typeof usage === 'object') {
      if (usage.total_tokens !== undefined) count = usage.total_tokens;
      else {
        const input = usage.input_tokens ?? usage.prompt_tokens;
        const output = usage.output_tokens ?? usage.completion_tokens;
        if (input === undefined && output === undefined) return '';
        count = Number(input || 0) + Number(output || 0);
      }
    }
    return Number.isFinite(Number(count)) ? Number(count).toLocaleString() + ' tokens' : '';
  }
  function renderQueries(body, result) {
    const calls = Array.isArray(result.calls) ? result.calls : Array.isArray(result.query_traces) ? result.query_traces : [];
    if (!calls.length) { body.append(el('p', {class: 'notice'}, taskMode() === 'mock' ? '离线演示不发起外部 API 调用。' : '尚无查询记录。')); return; }
    calls.forEach(call => {
      const failed = ['failed', 'error', 429].includes(call.status);
      const status = call.provider === 'cnki' && call.status === 'no_results' ? '公开网页检索未命中' : failed && String(call.operation || '').includes('fetch') ? '候选待核验' : ({ok: '成功', no_results: '未找到结果', mock: '模拟', error: '请求失败', deferred: '已退避', cached: '缓存'})[call.status] || str(call.status || call.outcome || '已记录');
      body.append(el('article', {class: 'query-item'},
        el('div', {class: 'query-header'}, el('span', {}, call.provider || call.source || '来源', call.operation || call.method ? ' / ' + (call.operation || call.method) : ''), badge(status, failed ? 'danger' : 'neutral')),
        el('div', {class: 'query-text'}, str(call.query || call.params || call.message || call.error || '')),
        el('div', {class: 'source-meta'}, [call.created_at || call.at || call.retrieved_at ? date(call.created_at || call.at || call.retrieved_at, true) : '', call.latency_ms !== undefined ? call.latency_ms + ' ms' : '', call.cache_hit || call.cached ? '缓存命中' : '', tokenLabel(call.tokens)].filter(Boolean).join(' · '))));
    });
  }
  function openUpload() {
    $('upload-error').textContent = ''; $('parent-task-label').hidden = !state.task; $('link-parent').checked = !!state.task;
    dialog('upload-dialog');
  }
  $('new-task').addEventListener('click', openUpload); $('welcome-upload').addEventListener('click', openUpload);
  $('bib-file').addEventListener('change', () => { $('upload-filename').textContent = $('bib-file').files[0]?.name || '选择或拖入 .bib 文件'; });
  $('drop-zone').addEventListener('dragover', e => { e.preventDefault(); $('drop-zone').classList.add('dragging'); });
  $('drop-zone').addEventListener('dragleave', () => $('drop-zone').classList.remove('dragging'));
  $('drop-zone').addEventListener('drop', e => { e.preventDefault(); $('drop-zone').classList.remove('dragging'); if (e.dataTransfer.files.length) { $('bib-file').files = e.dataTransfer.files; $('bib-file').dispatchEvent(new Event('change')); } });
  $('upload-form').addEventListener('change', () => { const mode = new FormData($('upload-form')).get('mode'); $('start-upload').replaceChildren(mode === 'live' ? '开始在线核验' : '开始离线演示', icon('arrow')); });
  $('upload-form').addEventListener('submit', async e => {
    e.preventDefault(); const file = $('bib-file').files[0]; if (!file) return;
    if (file.size > 2 * 1024 * 1024) { $('upload-error').textContent = '文件不得超过 2 MB。'; return; }
    if (!file.name.toLowerCase().endsWith('.bib')) { $('upload-error').textContent = '请选择 .bib 文件。'; return; }
    const data = new FormData($('upload-form')); if (state.task && $('link-parent').checked) data.set('parent_id', state.task.id);
    const submit = $('start-upload'); submit.disabled = true; $('upload-error').textContent = '';
    try { const task = await api('/api/tasks', {method: 'POST', body: data}); closeDialog('upload-dialog'); $('upload-form').reset(); $('upload-filename').textContent = '选择或拖入 .bib 文件'; $('start-upload').replaceChildren('开始离线演示', icon('arrow')); await loadTask(task.id || task.task?.id, true); try { await refreshHistory(); } catch { /* The created task is durable even if history cannot refresh. */ } toast('检查已创建，原文件保持不变。'); }
    catch (error) { $('upload-error').textContent = error.message; } finally { submit.disabled = false; }
  });
  $('entry-search').addEventListener('input', e => { state.query = e.target.value.toLowerCase().trim(); renderEntries(); });
  $('entry-filter').addEventListener('change', e => { state.filter = e.target.value; renderEntries(); });
  async function taskOperation(path, text) { try { const task = await api(path, {method: 'POST'}); await loadTask(task.id || task.task?.id || state.task.id, false); try { await refreshHistory(); } catch { /* Do not repeat a completed write. */ } toast(task.unchanged ? '没有需要刷新的过期结果。' : text); } catch (error) { toast(error.message, true); } }
  async function cancelTask() { if (!state.task) return; if (await confirmAction('取消当前检查？', '已完成的结果和证据将保留。正在执行的请求可能仍会完成。', '取消检查')) await taskOperation('/api/tasks/' + encodeURIComponent(state.task.id) + '/cancel', '已请求取消'); }
  async function retryTask() { if (!state.task) return; if (taskMode() === 'live' && !(await confirmAction('重试失败条目', '仅重试失败或未完成条目，可能调用已配置的在线来源并产生费用。', '重试'))) return; await taskOperation('/api/tasks/' + encodeURIComponent(state.task.id) + '/retry', '已创建重试任务'); }
  async function refreshStale() { if (!state.task) return; if (!(await confirmAction('刷新过期结果', taskMode() === 'live' ? '仅复查证据已过期的条目。将重新查询相关在线来源，可能产生费用；历史任务和旧证据保留。' : '仅刷新过期的模拟结果，不发起外部请求。历史任务和旧证据保留。', '刷新过期结果'))) return; await taskOperation('/api/tasks/' + encodeURIComponent(state.task.id) + '/refresh-stale', '已提交过期结果刷新'); }
  async function refreshEntry() {
    const entry = selectedEntry(); if (!entry) return;
    const description = taskMode() === 'live' ? '将重新查询本条文献的全部已启用来源：Crossref、DOI / DataCite、arXiv、Semantic Scholar，以及已配置的 DeepSeek / 大模型和已启用的 CNKI 分支。可能产生费用；刷新会创建新任务，不改写旧证据。' : '将重新生成本条文献的模拟证据，不发起外部请求。刷新会创建新任务，不改写历史证据。';
    if (!(await confirmAction('刷新 ' + entry.citation_key + '？', description, '刷新当前条目'))) return;
    try { const task = await api('/api/entries/' + encodeURIComponent(entry.id) + '/refresh', {method: 'POST', body: {sources: ['all']}}); await loadTask(task.id || task.task?.id, false); try { await refreshHistory(); } catch { /* Do not repeat a completed write. */ } toast('已创建条目刷新任务'); } catch (error) { toast(error.message, true); }
  }
  async function deleteTask() {
    const task = state.task; if (!task) return;
    if (!(await confirmAction('删除这次检查？', '将删除“' + task.filename + '”的任务记录、上传快照及其检查结果，此操作不可撤销。本地原文件和其他任务不受影响。', '删除检查'))) return;
    try { await api('/api/tasks/' + encodeURIComponent(task.id), {method: 'DELETE'}); state.task = null; state.selected = null; state.taskStamp = ''; await refreshHistory(); if (state.tasks.length) await loadTask(state.tasks[0].id, true); else { $('task-workspace').hidden = true; $('welcome').hidden = false; $('task-title').textContent = '引文，逐条有据。'; $('task-subtitle').textContent = '从一份 BibTeX 开始。'; $('task-actions').replaceChildren(); try { localStorage.removeItem('cite-audit.task'); } catch {} } toast('检查记录已删除'); } catch (error) { toast(error.message, true); }
  }
  function openManual() { if (!selectedEntry()) return; $('manual-form').reset(); $('manual-error').textContent = ''; dialog('manual-dialog'); }
  $('manual-file').addEventListener('change', async e => { const file = e.target.files[0]; if (!file) return; if (file.size > 200000) { $('manual-error').textContent = '题录文件不得超过 200 KB。'; return; } try { $('manual-form').elements.text.value = await file.text(); $('manual-form').elements.format.value = file.name.toLowerCase().endsWith('.ris') ? 'ris' : file.name.toLowerCase().endsWith('.bib') ? 'bibtex' : 'text'; } catch { $('manual-error').textContent = '无法读取题录文件。'; } });
  $('manual-form').addEventListener('submit', async e => {
    e.preventDefault(); const entry = selectedEntry(); if (!entry) return; const data = Object.fromEntries(new FormData(e.target)); const submit = e.target.querySelector('[type=submit]');
    if (!String(data.text).trim() && !String(data.source_url).trim()) { $('manual-error').textContent = '请提供题录文本或来源链接。'; return; }
    if (data.source_url && !safeURL(data.source_url)) { $('manual-error').textContent = '仅支持不含凭据的 HTTP(S) 来源链接。'; return; }
    if (!String(data.text).trim()) data.format = 'text'; submit.disabled = true; $('manual-error').textContent = '';
    try { const task = await api('/api/entries/' + encodeURIComponent(entry.id) + '/manual', {method: 'POST', body: data}); closeDialog('manual-dialog'); await loadTask(task.id || task.task?.id, false); try { await refreshHistory(); } catch { /* Do not repeat a completed write. */ } state.tab = 'evidence'; renderDetail(); toast('人工证据已保存，未标记为在线核验。'); } catch (error) { $('manual-error').textContent = error.message; } finally { submit.disabled = false; }
  });
  function setSettings(settings) {
    state.settings = settings; const form = $('settings-form');
    form.querySelectorAll('input[name]').forEach(input => { const [group, name] = input.name.split('.'); if (name === 'api_key') input.value = ''; else input.value = settings[group]?.[name] ?? ''; });
    ['llm', 'deepseek', 'semantic_scholar'].forEach(provider => { const node = $(provider + '-key-state'); const configured = settings[provider]?.api_key_configured; node.textContent = configured ? '密钥已配置' : '未配置密钥'; node.className = 'badge ' + (configured ? 'good' : 'neutral'); const fromEnv = settings[provider]?.api_key_from_env; if (fromEnv) node.textContent = '环境变量已配置'; const remove = document.querySelector('[data-remove-key="' + provider + '"]'); remove.hidden = !configured || !!fromEnv; });
  }
  $('settings-button').addEventListener('click', async () => { $('settings-error').textContent = ''; dialog('settings-dialog'); try { setSettings(await api('/api/settings')); } catch (error) { $('settings-error').textContent = error.message; } });
  $('settings-dialog').addEventListener('close', () => { $('settings-form').querySelectorAll('input[type=password]').forEach(input => { input.value = ''; }); });
  $('settings-form').addEventListener('submit', async e => {
    e.preventDefault(); const payload = {}; const submit = $('save-settings'); submit.disabled = true; $('settings-error').textContent = '';
    for (const input of e.target.querySelectorAll('input[name]')) { if (input.value === '') continue; const [group, name] = input.name.split('.'); if (!payload[group]) payload[group] = {}; payload[group][name] = input.type === 'number' ? Number(input.value) : input.value.trim(); }
    try { await api('/api/settings', {method: 'PUT', body: payload}); setSettings(await api('/api/settings')); toast('设置已保存'); } catch (error) { $('settings-error').textContent = error.message; } finally { submit.disabled = false; }
  });
  document.querySelectorAll('[data-test]').forEach(b => b.addEventListener('click', async () => {
    const provider = b.dataset.test, result = $('test-' + provider); result.textContent = ''; result.className = 'test-result';
    if (!(await confirmAction('测试 ' + ({llm: '大模型', deepseek: 'DeepSeek', semantic_scholar: 'Semantic Scholar', crossref: 'Crossref', arxiv: 'arXiv'}[provider]) + ' 连接', '使用已保存的配置发起真实测试请求，可能产生调用费用。尚未保存的修改不会用于本次测试。', '开始测试'))) return;
    b.disabled = true; result.textContent = '测试中…';
    try { const response = await api('/api/settings/test/' + encodeURIComponent(provider), {method: 'POST'}); result.textContent = response.message || response.status || (response.ok ? '连接成功' : '测试失败'); result.className = 'test-result ' + (response.ok ? 'good' : 'bad'); } catch (error) { result.textContent = error.message; result.className = 'test-result bad'; } finally { b.disabled = false; }
  }));
  $('clear-cache').addEventListener('click', async () => {
    if (!(await confirmAction('清除查询缓存？', '仅删除可复用的查询缓存。任务历史、上传文件及历史证据均保留。下次检查可能重新调用在线来源并产生费用。', '清除缓存'))) return;
    try { const result = await api('/api/cache/clear', {method: 'POST'}); toast('已清除 ' + (result.deleted ?? '') + ' 条缓存，历史证据保留。'); } catch (error) { toast(error.message, true); }
  });
  document.querySelectorAll('[data-remove-key]').forEach(b => b.addEventListener('click', async () => {
    const provider = b.dataset.removeKey; if (!(await confirmAction('移除已保存密钥？', '将移除本机保存的 ' + provider + ' API key。不会撤销服务商端密钥，环境变量中的密钥也不受影响。', '移除密钥'))) return;
    try { await api('/api/settings', {method: 'PUT', body: {[provider]: {api_key: null}}}); setSettings(await api('/api/settings')); toast('已移除本机保存的密钥'); } catch (error) { toast(error.message, true); }
  }));
  $('clear-manual').addEventListener('click', async () => {
    if (!(await confirmAction('删除人工导入记录？', '删除所有可供后续检查复用的人工题录。历史任务中的证据快照仍保留，需单独删除对应任务才会移除。此操作不可撤销。', '删除人工题录'))) return;
    try { const result = await api('/api/manual-records', {method: 'DELETE'}); toast('已删除 ' + (result.deleted ?? '') + ' 条人工记录，历史证据保留。'); } catch (error) { toast(error.message, true); }
  });
  async function poll() {
    clearTimeout(pollTimer);
    if (pollBusy || document.hidden) { pollTimer = setTimeout(poll, 1000); return; }
    pollBusy = true;
    try { if (!state.task && !$('connection-error').hidden && Date.now() >= reconnectAt) { reconnectAt = Date.now() + 5000; await refreshHistory(); if (state.tasks.length) await loadTask(state.tasks[0].id, true, true); else $('connection-error').hidden = true; } else if (state.task && isRunning()) await loadTask(state.task.id, false, true); else if (state.tasks.some(isRunning)) { await refreshHistory(); if (state.task) await loadTask(state.task.id, false, true); } } catch { /* Existing task remains visible during a reconnect. */ }
    finally { pollBusy = false; clearTimeout(pollTimer); pollTimer = setTimeout(poll, 1000); }
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { clearTimeout(pollTimer); poll(); } });
  async function init() {
    try {
      await refreshHistory(); let last; try { last = localStorage.getItem('cite-audit.task'); } catch {}
      const task = state.tasks.find(t => String(t.id) === last) || state.tasks[0]; if (task) await loadTask(task.id, true);
    } catch (error) { $('connection-error').textContent = '无法连接本地服务：' + error.message; $('connection-error').hidden = false; }
    pollTimer = setTimeout(poll, 1000);
  }
  init();
})();

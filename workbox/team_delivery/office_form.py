"""Same-origin native Kanboard handoff forms, in the embedded browser's isolated world."""

from workbox.team_delivery.office_identity import _PROFILE_LINK


INSTALL = r"""(function(origin, user) {
    if (location.origin !== origin) throw Error('办公室地址已改变。');
""" + _PROFILE_LINK + r"""
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
    const state = {bytes: [], form: null, filename: '', id: '', sent:false};
    const controller = new AbortController();
    function fail(message) { throw Error(message); }
    function route(name, id, extra={}) {
        const result = new URL('/', origin);
        result.search = new URLSearchParams({controller: name, action:'show', plugin:'WorkBoxIntake',
            ...(id ? {business_id:id} : {}), ...extra});
        return result;
    }
    function exact(href, target) {
        const u = new URL(href, origin);
        if (u.origin !== origin || u.hash || u.username || u.password ||
            !['/', '/index.php'].includes(u.pathname)) return false;
        const a = u.searchParams, b = target.searchParams;
        return Array.from(a).length === Array.from(b).length &&
            Array.from(b).every(([k,v]) => a.getAll(k).length === 1 && a.get(k) === v);
    }
    function account(doc, url) {
        if (profileId(doc, menu, ownLabels, url) !== user) fail('办公室账号已变化，请重新核对。');
    }
    async function get(url, options={}) {
        if (location.origin !== origin || new URL(url).origin !== origin) fail('办公室地址已改变。');
        const response = await fetch(url, {credentials:'same-origin', redirect:'error', mode:'same-origin',
            cache:'no-store', ...options, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30000)])});
        if (!response.ok) fail('办公室未确认操作，请在团队办理查看该项目。');
        return response;
    }
    async function documentAt(url) {
        const r = await get(url), doc = document.createElement('template');
        doc.innerHTML = await r.text();
        account(doc.content, url.href);
        return doc.content;
    }
    async function target(title) {
        const ids = new Set();
        for (let page=1; page<=10000; page++) {
            const url = route('SimpleUploadsController', '', {page:String(page)});
            const doc = await documentAt(url);
            const section = doc.querySelector('[data-workbox-simple-page="uploads"]');
            if (!section) fail('无法核对办公室待办。');
            for (const row of section.querySelectorAll('tbody tr')) {
                const link = row.querySelector('td:first-child a[href]');
                if (!link || link.textContent.trim() !== title || row.cells[1]?.textContent.trim() !== '手册') continue;
                const id = new URL(link.getAttribute('href'), origin).searchParams.get('business_id');
                if (!uuid.test(id) || !exact(link.getAttribute('href'), route('SimpleUploadsController', id)))
                    fail('办公室待办编号无法核实。');
                ids.add(id);
            }
            const more = [...section.querySelectorAll('.pagination a[href]')].find(a => a.textContent.trim() === '下一页');
            if (!more) return ids.size === 1 ? [...ids][0] : '';
            if (!exact(more.getAttribute('href'), route('SimpleUploadsController','',{page:String(page+1)})))
                fail('办公室待办分页已变化。');
        }
        fail('办公室任务列表未读取完整。');
    }
    async function prepare(id, title, filename, recovery) {
        if (!uuid.test(id)) fail('项目编号无效。');
        state.id = id; state.filename = filename;
        if (recovery) return true;
        const url = route('SimpleUploadsController', id), doc = await documentAt(url);
        const section = doc.querySelector('[data-workbox-simple-page="uploads"]');
        if (section?.querySelector(':scope > p.wb-simple-filename strong')?.textContent.trim() !== title)
            fail('办公室项目名已变化。');
        const action = route('SimpleUploadsController', id, {action:'handoffManual'});
        const forms = [...section.querySelectorAll('form')].filter(f =>
            f.method === 'post' && f.enctype === 'multipart/form-data' && exact(f.getAttribute('action'), action));
        const f = forms[0];
        if (forms.length !== 1 || f.querySelectorAll('input[type=file][name=manual_file]:enabled').length !== 1 ||
            ![...f.querySelectorAll('button[type=submit]:enabled')].some(b => b.textContent.trim() === '上传并交接') ||
            ['csrf_token','observed','expected_maintenance_generation','request_key'].some(key =>
                f.querySelectorAll('input[name="'+key+'"]').length !== 1 || !f.elements[key].value))
            fail('当前工序无法自动交接，请在团队办理中核对。');
        state.form = new FormData(f);
        state.action = action;
        return true;
    }
    async function write(token) {
        await documentAt(route('SimpleUploadsController', state.id));
        const form = state.form;
        state.form = null;
        if (!form || !state.bytes.length) fail('手册或交接表单不完整。');
        form.set('manual_file', new Blob(state.bytes, {type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}), state.filename);
        if (controller.signal.aborted) fail('本次交接已取消。');
        state.sent = true;
        const response = await get(state.action, {method:'POST', body:form,
            headers:token ? {'X-WorkBox-Auto':token} : {}});
        await response.text();
        return true;
    }
    async function verify() {
        const doc = await documentAt(route('CodeWorkController', state.id, {simple:'1'}));
        const links = [...doc.querySelectorAll('[data-workbox-code-panel] a[href]')].filter(
            a => a.textContent.trim() === '下载本次交接手册');
        if (links.length !== 1) return false;
        const href = new URL(links[0].getAttribute('href'), origin);
        const material = href.searchParams.get('material_id');
        if (!uuid.test(material) || !exact(href, route('MaterialDownloadController',state.id,
            {action:'material', material_id:material}))) return false;
        const response = await get(href);
        const reader = response.body.getReader();
        let index=0, offset=0;
        try {
            for (;;) {
                const {done, value} = await reader.read();
                if (done) return index === state.bytes.length && offset === 0;
                for (const byte of value) {
                    if (!state.bytes[index] || state.bytes[index][offset++] !== byte) return false;
                    if (offset === state.bytes[index].length) {index++; offset=0;}
                }
            }
        } finally { await reader.cancel(); }
    }
    globalThis.officeTransfer = {target, prepare, write, verify, wasSent:() => state.sent,
        append(encoded) {state.bytes.push(Uint8Array.from(atob(encoded), c=>c.charCodeAt(0))); return true;},
        clear() {controller.abort(); state.bytes=[]; state.form=null; delete globalThis.officeTransfer; return true;}
    };
})"""

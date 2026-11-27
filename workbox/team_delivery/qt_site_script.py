"""Isolated-world scripts for the original site's authenticated material workflow."""


OBSERVE = r'''(() => {
    let interaction = 0;
    let accountVersion = 0;
    const label = () => document.getElementById('who')?.textContent.trim() || '';
    let previous = label();
    new MutationObserver(() => {
        const next = label();
        if (next !== previous) {
            previous = next;
            accountVersion++;
            globalThis.workboxCompletedView = null;
        }
    }).observe(document, {subtree: true, childList: true, characterData: true});
    for (const event of ['pointerdown', 'keydown', 'input', 'change', 'submit', 'wheel'])
        document.addEventListener(event, e => { if (e.isTrusted) interaction++; }, true);
    document.addEventListener('click', e => {
        if (e.isTrusted && e.target.closest?.('#matCompletedToggle,[data-action="collapse-completed"][data-stage="mat"]'))
            globalThis.workboxCompletedView = null;
    }, true);
    globalThis.workboxSiteObservation = () => ({label: label(), accountVersion, interaction});
})();'''


OPERATE = r'''async ({origin, operation, expected, deadline, remaining, requestKey, writeToken, personal}) => {
    if (location.origin !== origin || self !== top || !globalThis.workboxSiteObservation)
        return {error: 'origin'};
    const observe = globalThis.workboxSiteObservation;
    const initial = observe();
    const abort = new AbortController();
    const pending = globalThis.workboxSitePending ??= new Map();
    pending.set(requestKey, abort);
    const end = performance.now() + remaining;
    const stamp = () => !globalThis.workboxSiteClosed && !abort.signal.aborted &&
        Date.now() < deadline && performance.now() < end;
    const networkSignal = () => AbortSignal.any([abort.signal, AbortSignal.timeout(4200)]);
    const key = value => typeof value === 'string' ? value.normalize('NFC').trim().toLowerCase() : '';
    const sameDocumentAccount = () => {
        const now = observe();
        return stamp() && location.origin === origin && initial.accountVersion === now.accountVersion &&
            initial.label === now.label;
    };
    const get = async path => {
        const response = await fetch(path, {credentials: 'same-origin', mode: 'same-origin',
            cache: 'no-store', redirect: 'error', signal: networkSignal()});
        if (response.status === 401) return null;
        if (!response.ok) throw new Error('read');
        return response.json();
    };
    const visible = e => e && e.getClientRects().length > 0 &&
        !['hidden', 'collapse'].includes(getComputedStyle(e).visibility);
    const identity = async () => {
        const me = (await get('/api/me'))?.user;
        if (!sameDocumentAccount()) throw new Error('account');
        if (!me || me.mustChange || !key(me.account) || typeof me.name !== 'string' || !me.name.trim() ||
            !['材料', '代码'].includes(me.role)) return null;
        if (initial.label !== me.name.trim() + ' · ' + me.role) throw new Error('account');
        return {username: me.account, name: me.name.trim(), role: me.role, version: initial.accountVersion};
    };
    const equivalent = (a, b) => a && b && key(a.username) === key(b.username) &&
        a.name === b.name && a.role === b.role && a.version === b.version;
    const owned = (project, account) => key(project.matOwner) === key(account.username);
    const text = value => typeof value === 'string' ? value.trim() : '';
    const urgencies = new Set(['非常紧急', '比较紧急', '紧急', '正常']);
    const defaultUrgency = project => /预审|加急/.test(text(project.type)) ? '比较紧急' : '正常';
    const chronology = (a, b) => {
        const created = project => text(project.createdAt || project.created).replace('T', ' ');
        return created(a).localeCompare(created(b)) || Number(a.id) - Number(b.id);
    };
    const batches = rows => {
        const result = new Map();
        for (const project of rows) {
            const id = text(project?.batchId);
            if (!id) continue;
            if (!result.has(id)) result.set(id, {namedProject: null, urgency: '', defaultUrgency: '正常'});
            const batch = result.get(id);
            if (text(project.batchName) && (!batch.namedProject || chronology(project, batch.namedProject) < 0))
                batch.namedProject = project;
            if (urgencies.has(project.batchUrgency)) batch.urgency = project.batchUrgency;
            if (defaultUrgency(project) === '比较紧急') batch.defaultUrgency = '比较紧急';
        }
        return result;
    };
    const data = (project, batchById) => {
        const batchId = text(project.batchId);
        const batch = batchById.get(batchId);
        return {
            id: String(project.id), title: project.name, status: project.matStatus,
            owner: project.matOwnerName || project.matOwner,
            updated: typeof project.lastOperatedAt === 'string' ? project.lastOperatedAt :
                (typeof project.updatedAt === 'string' ? project.updatedAt : ''),
            batchId,
            batchName: text(batch?.namedProject?.batchName) || text(project.batchName),
            urgency: batch ? batch.urgency || batch.defaultUrgency :
                (urgencies.has(project.batchUrgency) ? project.batchUrgency : defaultUrgency(project)),
        };
    };
    const projects = async account => {
        const result = await get('/api/state');
        if (!Array.isArray(result?.projects) || !equivalent(account, await identity())) throw new Error('account');
        return result.projects;
    };
    const target = (rows, account, batchById) => {
        const matching = rows.filter(row => String(row.id) === expected.id);
        if (matching.length !== 1 || matching[0].name !== expected.title || !owned(matching[0], account))
            throw new Error('target');
        return data(matching[0], batchById);
    };
    const pageBusy = () => {
        const focus = document.activeElement;
        const editing = visible(focus) && (focus.isContentEditable ||
            focus.matches('textarea,select,input:not([type="checkbox"]):not([type="radio"])'));
        return !sameDocumentAccount() || observe().interaction !== initial.interaction || editing ||
            Boolean(document.querySelector('dialog[open],input.row-check:checked,input.mat-check:checked,' +
                'input.code-check:checked,input[data-select-batch]:checked,#matList[aria-busy="true"],' +
                '#refreshStatus[data-state="loading"]')) ||
            [...document.querySelectorAll('[role="dialog"],[role="alertdialog"],#matFilter[open],#matType[open]')]
                .some(visible);
    };
    const materialList = () => document.getElementById('matList');
    const completedToggle = () => document.getElementById('matCompletedToggle');
    const workboxExpanded = () => globalThis.workboxCompletedView === completedToggle();
    const filtered = (includeWorkboxView = false) => ['matSearch','matRemark','matCodeOwner','matMaterialOwner',
        'matCreatedDate','matOperationDate'].some(id => document.getElementById(id)?.value?.trim()) ||
        Boolean(document.querySelector('#matFilter input:checked,#matType input:checked,' +
            '#matMine:not(:checked),#matBatchFocus:not(.hidden):not([hidden]),' +
            '#matReminders [data-stage-reminder="overdue"][aria-pressed="true"]')) ||
        (completedToggle()?.getAttribute('aria-expanded') === 'true' && !(includeWorkboxView && workboxExpanded()));
    const openMaterials = () => {
        if (visible(materialList())) return true;
        const tabs = [...document.querySelectorAll('button[data-page="material"]')].filter(visible);
        if (tabs.length !== 1) return false;
        tabs[0].click();
        return visible(materialList());
    };
    const reveal = (before, includeWorkboxView = false) => {
        if (!visible(materialList()) && (filtered(includeWorkboxView) || !openMaterials())) return false;
        const visited = new Set();
        for (let attempt = 0; attempt < 40 && !pageBusy() && visible(materialList()); attempt++) {
            const boxes = [...materialList().querySelectorAll('input.mat-check[type="checkbox"]')]
                .filter(box => box.value === expected.id && visible(box));
            if (boxes.length) {
                const row = boxes.length === 1 && boxes[0].closest('tr');
                const name = row && row.querySelector('td:nth-child(2) span.project-name');
                if (!name || name.textContent !== expected.title || boxes[0].dataset.status !== before.status ||
                    row.cells[6]?.textContent.trim() !== before.owner) return false;
                row.scrollIntoView({block: 'center'});
                return true;
            }
            if (filtered(includeWorkboxView)) return false;
            const pager = document.getElementById('matPager');
            const states = [...(pager?.innerText || '').matchAll(/第\s*(\d+)\s*\/\s*(\d+)\s*页/g)];
            if (states.length !== 1) return false;
            const page = Number(states[0][1]);
            const total = Number(states[0][2]);
            if (page < 1 || page > total || total > 20) return false;
            visited.add(page);
            const remaining = Array.from({length: total}, (_, index) => index + 1)
                .filter(number => !visited.has(number));
            if (!remaining.length) return false;
            const direction = remaining.some(number => number > page) ? '下一页' : '上一页';
            const buttons = [...pager.querySelectorAll('button[data-action="page"][data-kind="mat"]')]
                .filter(button => visible(button) && !button.disabled && button.textContent.trim() === direction);
            if (buttons.length !== 1) return false;
            buttons[0].click();
        }
        return false;
    };
    const restoreMine = () => {
        const mine = document.getElementById('matMine');
        if (mine && !mine.checked) {
            mine.checked = true;
            mine.dispatchEvent(new Event('change', {bubbles: true}));
        }
    };
    const showCompletion = async before => {
        if (before.status !== '已完成' || filtered(true) || !openMaterials()) return false;
        const refresh = document.getElementById('refreshApp');
        const progress = document.getElementById('refreshStatus');
        const mine = document.getElementById('matMine');
        if (!visible(refresh) || refresh.matches(':disabled,[aria-disabled="true"]') ||
            !progress || !mine?.checked || pageBusy()) return false;
        refresh.click();
        // The site's native refresh resets its filters synchronously. Restore "only mine"
        // before waiting for the result, even if the read fails or the operator moves on.
        if (sameDocumentAccount()) restoreMine();
        if (progress.dataset.state !== 'loading') return false;
        const waitUntil = performance.now() + 4500;
        while (progress.dataset.state === 'loading' && sameDocumentAccount() &&
               observe().interaction === initial.interaction && performance.now() < waitUntil)
            await new Promise(resolve => setTimeout(resolve, 50));
        if (progress.dataset.state !== 'success' || pageBusy()) return false;
        restoreMine();
        if (filtered(true)) return false;
        const toggle = completedToggle();
        if (!visible(toggle) || toggle.matches(':disabled,[aria-disabled="true"]')) return false;
        if (toggle.getAttribute('aria-expanded') !== 'true') {
            toggle.click();
            if (toggle.getAttribute('aria-expanded') !== 'true') return false;
            globalThis.workboxCompletedView = toggle;
        }
        return reveal(before, true);
    };
    let sent = false;
    try {
        if (!initial.label || initial.label === '未登录') return {account: null, rows: []};
        const account = await identity();
        if (operation === 'identity' || !account || account.role !== '材料') return {account, rows: []};
        if (expected && !equivalent(account, expected.account)) return {error: 'account'};
        const rows = await projects(account);
        const batchById = batches(rows);
        if (operation === 'snapshot')
            return {account, rows: rows.filter(row => owned(row, account)).map(row => data(row, batchById))};
        const before = target(rows, account, batchById);
        if (operation === 'target') return {account, target: before};
        if (before.status !== expected.status || before.owner !== expected.owner || before.updated !== expected.updated)
            return {error: 'target'};
        if (pageBusy()) return {error: 'busy'};
        if (operation === 'show-personal-completion')
            return {account, target: {...before, visible: await showCompletion(before)}};
        if (operation === 'reveal') {
            return reveal(before) ? {account, target: before} : {error: 'not-visible'};
        }
        if (filtered(personal)) return {error: 'busy'};
        const id = Number(expected.id);
        if (!Number.isSafeInteger(id) || id < 1 || String(id) !== expected.id) return {error: 'target'};
        const allowedStart = operation === 'start-material' && ['待制作', '预审退回'].includes(before.status);
        const allowedComplete = operation === 'complete-material' && before.status === '进行中';
        if (!allowedStart && !allowedComplete) return {error: 'target'};
        const desired = allowedStart ? '进行中' : '已完成';
        const headers = {'Content-Type': 'application/json'};
        if (personal && !writeToken) return {error: 'account'};
        if (writeToken) headers[personal ? 'X-WorkBox-Personal' : 'X-WorkBox-Auto'] = writeToken;
        sent = true;
        const reply = await fetch('/api/' + operation, {method: 'POST', mode: 'same-origin', credentials: 'same-origin',
            headers, body: JSON.stringify({ids: [id]}),
            cache: 'no-store', redirect: 'error', signal: networkSignal()});
        const receipt = reply.ok ? await reply.json() : null;
        if (!receipt || typeof receipt !== 'object' || Array.isArray(receipt) || receipt.error ||
            (receipt.ok !== undefined && receipt.ok !== true)) return {error: 'uncertain'};
        const refreshed = await projects(account);
        const after = target(refreshed, account, batches(refreshed));
        return after.status === desired ? {account, target: after} : {error: 'uncertain'};
    } catch (_) {
        return {error: sent ? 'uncertain' : 'read'};
    } finally {
        pending.delete(requestKey);
    }
}'''

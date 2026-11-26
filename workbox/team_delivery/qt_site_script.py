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
        }
    }).observe(document, {subtree: true, childList: true, characterData: true});
    for (const event of ['pointerdown', 'keydown', 'input', 'change', 'submit'])
        document.addEventListener(event, e => { if (e.isTrusted) interaction++; }, true);
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
    const data = project => ({id: String(project.id), title: project.name, status: project.matStatus,
        owner: project.matOwnerName || project.matOwner,
        updated: typeof project.lastOperatedAt === 'string' ? project.lastOperatedAt :
            (typeof project.updatedAt === 'string' ? project.updatedAt : '')});
    const projects = async account => {
        const result = await get('/api/state');
        if (!Array.isArray(result?.projects) || !equivalent(account, await identity())) throw new Error('account');
        return result.projects;
    };
    const target = (rows, account) => {
        const matching = rows.filter(row => String(row.id) === expected.id);
        if (matching.length !== 1 || matching[0].name !== expected.title || !owned(matching[0], account))
            throw new Error('target');
        return data(matching[0]);
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
    const filtered = () => ['matSearch','matRemark','matCodeOwner','matMaterialOwner',
        'matCreatedDate','matOperationDate'].some(id => document.getElementById(id)?.value?.trim()) ||
        Boolean(document.querySelector('#matFilter input:checked,#matType input:checked,' +
            '#matMine:not(:checked),#matBatchFocus:not(.hidden):not([hidden]),' +
            '#matCompletedToggle[aria-expanded="true"],#matReminders [data-stage-reminder="overdue"][aria-pressed="true"]'));
    const reveal = before => {
        const materialList = () => document.getElementById('matList');
        if (!visible(materialList())) {
            if (filtered()) return false;
            const tabs = [...document.querySelectorAll('button[data-page="material"]')].filter(visible);
            if (tabs.length !== 1) return false;
            tabs[0].click();
        }
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
            if (filtered()) return false;
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
    let sent = false;
    try {
        if (!initial.label || initial.label === '未登录') return {account: null, rows: []};
        const account = await identity();
        if (operation === 'identity' || !account || account.role !== '材料') return {account, rows: []};
        if (expected && !equivalent(account, expected.account)) return {error: 'account'};
        const rows = await projects(account);
        if (operation === 'snapshot') return {account, rows: rows.filter(row => owned(row, account)).map(data)};
        const before = target(rows, account);
        if (operation === 'target') return {account, target: before};
        if (before.status !== expected.status || before.owner !== expected.owner || before.updated !== expected.updated)
            return {error: 'target'};
        if (pageBusy()) return {error: 'busy'};
        if (operation === 'reveal') {
            return reveal(before) ? {account, target: before} : {error: 'not-visible'};
        }
        if (filtered()) return {error: 'busy'};
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
        const after = target(await projects(account), account);
        return after.status === desired ? {account, target: after} : {error: 'uncertain'};
    } catch (_) {
        return {error: sent ? 'uncertain' : 'read'};
    } finally {
        pending.delete(requestKey);
    }
}'''

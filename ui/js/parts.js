/*
 * "This file holds more than one object. Which of them do you want?"
 *
 * A model saved on its own needs no such question, and does not get one. A Blender
 * project lit for rendering is another matter: it arrives with its studio floor and
 * its reflection cards, and until now all of it was welded onto the model and could
 * not be separated again.
 *
 * Nothing here guesses. Objects the file itself marks as not-for-render start
 * unticked — a rig's controller widgets say so in the file — and everything else
 * starts ticked, because the one real project that prompted this names its ground
 * plane "Studio ground - excluded from model validation" and carries no flag at all
 * to say so. A rule clever enough to catch that would also throw away somebody's
 * floor tile.
 *
 * What does the work instead is the grouping: Blender's own collections come
 * through, so 108 objects are six lines, and the studio goes away in one click.
 */
(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    const api = () => (window.pywebview && window.pywebview.api) || null;

    let parts = [];
    let settle = null;               // resolves the promise the caller is waiting on

    const text = s => String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const count = n => Number(n || 0).toLocaleString();
    const measure = s => (s && s.some(v => v > 0.0005))
        ? s.map(v => (+v).toFixed(v >= 100 ? 0 : 2)).join(' × ') : '';

    function groupsOf(list) {
        const order = [];
        const byName = new Map();
        for (const part of list) {
            const key = part.group || '';
            if (!byName.has(key)) { byName.set(key, []); order.push(key); }
            byName.get(key).push(part);
        }
        return order.map(name => ({ name, items: byName.get(name) }));
    }

    function draw() {
        const box = $('partsList');
        const groups = groupsOf(parts);
        const single = groups.length === 1 && !groups[0].name;
        box.innerHTML = groups.map((group, gi) => {
            const kept = group.items.filter(p => p.keep).length;
            const faces = group.items.reduce((n, p) => n + (p.faces || 0), 0);
            const head = single ? '' : `
                <div class="parts-group">
                    <label class="check">
                        <input type="checkbox" data-group="${gi}" ${kept === group.items.length ? 'checked' : ''}
                               ${kept && kept < group.items.length ? 'data-some="1"' : ''}>
                        <span>${text(group.name || 'Objects')}</span>
                    </label>
                    <span class="parts-group-meta">${group.items.length} object${group.items.length === 1 ? '' : 's'} · ${count(faces)} faces</span>
                </div>`;
            const rows = group.items.map(part => `
                <label class="parts-row${part.keep ? '' : ' off'}">
                    <input type="checkbox" data-id="${text(part.id)}" ${part.keep ? 'checked' : ''}>
                    <span class="parts-name" title="${text(part.name)}">${text(part.name)}</span>
                    <span class="parts-meta">${part.faces ? count(part.faces) + ' faces' : 'no geometry'}${
                        measure(part.size) ? ' · ' + measure(part.size) : ''}${
                        part.hidden ? ' · hidden in the file' : ''}</span>
                </label>`).join('');
            return head + `<div class="parts-rows">${rows}</div>`;
        }).join('');

        for (const input of box.querySelectorAll('input[data-id]')) {
            input.addEventListener('change', () => {
                const part = parts.find(p => String(p.id) === input.dataset.id);
                if (part) part.keep = input.checked;
                // Deliberately not a redraw. Rebuilding the list on every tick throws
                // away the very checkboxes being clicked, so a quick run down a column
                // loses every click after the first, and the scroll position with them.
                refresh();
            });
        }
        for (const input of box.querySelectorAll('input[data-group]')) {
            input.addEventListener('change', () => {
                const wanted = input.checked;
                for (const part of groups[+input.dataset.group].items) part.keep = wanted && !!part.faces;
                for (const row of box.querySelectorAll('input[data-id]')) {
                    const part = parts.find(p => String(p.id) === row.dataset.id);
                    if (part) row.checked = part.keep;
                }
                refresh();
            });
        }
        refresh();
    }

    /* Bring the ticks, the dimming and the totals back in line with `parts`,
       without touching the structure of the list. */
    function refresh() {
        const box = $('partsList');
        for (const input of box.querySelectorAll('input[data-id]')) {
            const part = parts.find(p => String(p.id) === input.dataset.id);
            if (!part) continue;
            input.checked = part.keep;
            input.closest('.parts-row').classList.toggle('off', !part.keep);
        }
        const groups = groupsOf(parts);
        for (const input of box.querySelectorAll('input[data-group]')) {
            const items = groups[+input.dataset.group].items;
            const kept = items.filter(p => p.keep).length;
            input.checked = kept > 0;
            input.indeterminate = kept > 0 && kept < items.length;
        }
        tally();
    }

    function tally() {
        const kept = parts.filter(p => p.keep);
        const faces = kept.reduce((n, p) => n + (p.faces || 0), 0);
        $('partsCount').textContent =
            `${count(kept.length)} of ${count(parts.length)} objects · ${count(faces)} faces`;
        $('btnPartsOpen').disabled = kept.length === 0;
    }

    function close(answer) {
        $('parts').classList.add('hidden');
        const done = settle;
        settle = null;
        if (done) done(answer);
    }

    /**
     * Ask, if there is anything worth asking about.
     *
     * Resolves to an array of object ids to keep, `null` to open the whole file
     * without asking, or `false` if the person backed out.
     */
    async function choose(path) {
        if (!api()) return null;
        let found;
        try {
            found = await api().inspect_model_parts(path);
        } catch (e) {
            return null;                       // cannot look inside: open it as before
        }
        if (!found || !found.success || !found.multi) return null;

        parts = (found.parts || []).filter(p => p.faces || p.hidden);
        if (parts.length < 2) return null;

        const name = path.split(/[\\/]/).pop();
        $('partsTitle').textContent = `${name} holds ${count(parts.length)} objects`;
        const rig = (found.armatures || [])[0];
        $('partsHint').textContent = rig
            ? `Choose what to open. This project is rigged (${rig.name}, ${count(rig.bones)} bones); `
              + 'the pose it is saved in is what comes across.'
            : 'Choose what to open. Everything you leave out stays in the file, untouched.';
        draw();
        $('parts').classList.remove('hidden');
        return new Promise(resolve => { settle = resolve; });
    }

    document.addEventListener('DOMContentLoaded', () => {
        $('btnPartsCancel').addEventListener('click', () => close(false));
        $('btnPartsClose').addEventListener('click', () => close(false));
        $('btnPartsAll').addEventListener('click', () => {
            for (const part of parts) part.keep = !!part.faces;
            draw();
        });
        $('btnPartsOpen').addEventListener('click', () =>
            close(parts.filter(p => p.keep).map(p => p.id)));
        $('parts').addEventListener('click', e => { if (e.target === $('parts')) close(false); });
        document.addEventListener('keydown', e => {
            if ($('parts').classList.contains('hidden')) return;
            if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); close(false); }
        }, true);
    });

    window.meshwrightParts = { choose };
})();

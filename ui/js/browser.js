/*
 * Meshwright's own Open dialog.
 *
 * Windows' dialog cannot show what a model looks like — Windows draws 3D
 * thumbnails through an app that is no longer part of it — so the standard
 * Open box is a list of identical icons and a filename to guess from. This one
 * asks the engine for a picture of whichever file is highlighted, which costs a
 * fraction of a second because nothing is loaded to make it (see
 * engine/quicklook.py).
 *
 * Two rules shape the code below:
 *   - Nothing waits for a picture. Rows appear at once, the picture arrives
 *     when it arrives, and every answer carries the request number it belongs
 *     to, so a slow file that the user has already moved past is dropped rather
 *     than drawn over what they are looking at now.
 *   - One request at a time. The engine is reached through a single bridge, so
 *     row thumbnails are fetched in a queue that stops the moment the folder
 *     changes — a folder of two hundred models never becomes two hundred
 *     outstanding calls.
 */
(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    const api = () => (window.pywebview && window.pywebview.api) || null;

    const MODEL_ICON = '<svg viewBox="0 0 24 24" class="row-icon"><path d="M12 2 3 7v10l9 5 9-5V7l-9-5Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M3 7l9 5 9-5M12 12v10" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    const FOLDER_ICON = '<svg viewBox="0 0 24 24" class="row-icon"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    const THUMB_ROWS = 80;          // rows thumbnailed on their own; the rest when clicked

    let entries = [];               // what is on screen, folders first
    let selected = -1;
    let folder = '';
    let parent = null;
    let atRecent = false;
    let token = 0;                  // bumped on every navigation: older answers are stale
    let lookToken = 0;
    let thumbRun = 0;
    let places = null;
    let opening = null;             // the callback that actually loads a file
    let looked = new Map();         // path -> what the engine said, so a second look is free
    let waiting = 0;                // engine calls the user is actually waiting on

    const text = s => String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const count = n => n.toLocaleString();
    const pause = ms => new Promise(r => setTimeout(r, ms));

    /*
     * Anything the user is waiting for goes through here, and the background
     * thumbnail queue stands aside while it does. Calls into the engine cross a
     * single bridge, so without this a folder of large models could leave a click
     * queued behind a dozen pictures nobody asked for yet.
     */
    async function foreground(work) {
        waiting++;
        try {
            return await work();
        } finally {
            waiting--;
        }
    }

    /* ---------- places down the left ---------- */
    function drawPlaces() {
        if (!places) return;
        const item = (label, path, kind, active) =>
            `<button class="place${active ? ' active' : ''}" data-path="${text(path)}" data-kind="${kind}">
                ${kind === 'recent' ? '&#9733;' : kind === 'drive' ? '&#128190;' : '&#128193;'}
                <span>${text(label)}</span>
             </button>`;
        const parts = [item('Recent', '', 'recent', atRecent)];
        for (const f of places.folders) parts.push(item(f.label, f.path, 'folder', !atRecent && sameFolder(f.path, folder)));
        if (places.drives.length) parts.push('<div class="place-head">This PC</div>');
        for (const d of places.drives) parts.push(item(d.label, d.path, 'drive', !atRecent && sameFolder(d.path, folder)));
        $('browserPlaces').innerHTML = parts.join('');
        for (const b of $('browserPlaces').querySelectorAll('.place')) {
            b.addEventListener('click', () => b.dataset.kind === 'recent' ? showRecent() : go(b.dataset.path));
        }
    }

    const sameFolder = (a, b) => (a || '').replace(/\\+$/, '').toLowerCase() === (b || '').replace(/\\+$/, '').toLowerCase();

    /* ---------- the list in the middle ---------- */
    function rowHtml(entry, index) {
        const meta = entry.is_dir ? 'Folder'
            : (entry.folder ? text(entry.folder) : `${entry.ext.replace('.', '').toUpperCase()} · ${entry.size_text}`);
        return `<div class="browse-row${index === selected ? ' selected' : ''}" data-i="${index}">
                    <span class="row-thumb" data-thumb="${text(entry.path)}">${entry.is_dir ? FOLDER_ICON : MODEL_ICON}</span>
                    <span class="row-name">${text(entry.name)}</span>
                    <span class="row-meta">${meta}</span>
                </div>`;
    }

    function draw() {
        const filter = $('browserFilter').value.trim().toLowerCase();
        const shown = [];
        entries.forEach((e, i) => { if (!filter || e.name.toLowerCase().includes(filter)) shown.push([e, i]); });
        const list = $('browserList');
        if (!shown.length) {
            list.innerHTML = `<p class="browse-empty">${filter ? 'Nothing here matches that.' : 'No models in this folder.'}</p>`;
        } else {
            list.innerHTML = shown.map(([e, i]) => rowHtml(e, i)).join('');
            for (const row of list.querySelectorAll('.browse-row')) {
                const index = +row.dataset.i;
                row.addEventListener('click', () => select(index));
                row.addEventListener('dblclick', () => activate(index));
            }
            // Pictures already in hand survive a redraw (filtering, mostly).
            for (const [e] of shown) {
                const known = looked.get(e.path);
                if (known && known.picture) paintThumb(e.path, known.picture);
            }
        }
        const models = entries.filter(e => !e.is_dir).length;
        const folders = entries.length - models;
        $('browserCount').textContent = atRecent
            ? `${count(models)} recently opened`
            : `${count(models)} model${models === 1 ? '' : 's'}${folders ? `, ${count(folders)} folder${folders === 1 ? '' : 's'}` : ''}`;
    }

    function select(index) {
        selected = index;
        for (const row of $('browserList').querySelectorAll('.browse-row')) {
            row.classList.toggle('selected', +row.dataset.i === index);
        }
        const entry = entries[index];
        $('btnBrowserOpen').disabled = !entry || entry.is_dir;
        if (!entry) return showPreview(null);
        if (entry.is_dir) return showPreview({ folderName: entry.name });
        lookAt(entry);
    }

    function activate(index) {
        const entry = entries[index];
        if (!entry) return;
        if (entry.is_dir) go(entry.path);
        else openFile(entry.path);
    }

    function scrollToSelected() {
        const row = $('browserList').querySelector('.browse-row.selected');
        if (row) row.scrollIntoView({ block: 'nearest' });
    }

    function move(step) {
        const rows = [...$('browserList').querySelectorAll('.browse-row')].map(r => +r.dataset.i);
        if (!rows.length) return;
        const at = rows.indexOf(selected);
        select(rows[Math.min(rows.length - 1, Math.max(0, (at < 0 ? 0 : at + step)))]);
        scrollToSelected();
    }

    /* ---------- the preview on the right ---------- */
    function showPreview(info) {
        const box = $('browserPreview');
        if (!info) {
            box.innerHTML = '<p class="browse-empty">Pick a file to see what is in it.</p>';
            return;
        }
        if (info.folderName) {
            box.innerHTML = `<div class="preview-shot folder">${FOLDER_ICON}</div>
                             <div class="preview-name">${text(info.folderName)}</div>
                             <p class="hint">Double-click to look inside.</p>`;
            return;
        }
        if (info.pending) {
            box.innerHTML = `<div class="preview-shot"><div class="preview-spin"></div></div>
                             <div class="preview-name">${text(info.name)}</div>
                             <p class="hint">Looking inside…</p>`;
            return;
        }
        if (info.success === false || (!info.format && !info.note)) {
            // The engine answers every file, so this is the unexpected case: say so
            // plainly rather than showing an empty panel that looks like a hang.
            box.innerHTML = `<div class="preview-shot"><div class="preview-none">${MODEL_ICON}</div></div>
                             <div class="preview-name">${text(info.name || '')}</div>
                             <p class="preview-note">${text(info.error || 'This file could not be read.')}</p>`;
            return;
        }
        const d = info.dimensions;
        const rows = [];
        if (info.format) rows.push(['Kind', text(info.format)]);
        rows.push(['Size on disk', text(info.size_text || '')]);
        if (info.faces) rows.push(['Triangles', count(info.faces)]);
        if (d) {
            rows.push(['Dimensions', `${d.approx ? '≈ ' : ''}${d.x} × ${d.y} × ${d.z} ${d.unit}`
                + (d.odd_scale ? ' <span class="preview-flag" title="No 3D format records what its numbers mean. Meshwright reads them as millimetres, as it does everywhere else — this model may have been saved in other units.">units?</span>' : '')]);
        }
        if (info.textures) rows.push(['Textures', count(info.textures)]);
        if (info.modified) rows.push(['Changed', text(info.modified)]);
        if (info.load_estimate) rows.push(['Opens in', text(info.load_estimate)]);

        box.innerHTML = `
            <div class="preview-shot">${info.picture
                ? `<img src="${info.picture}" alt="">`
                : `<div class="preview-none">${MODEL_ICON}</div>`}</div>
            <div class="preview-name" title="${text(info.path)}">${text(info.name)}</div>
            <table class="preview-facts">${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('')}</table>
            ${info.note ? `<p class="preview-note">${text(info.note)}</p>` : ''}`;
    }

    async function lookAt(entry) {
        const mine = ++lookToken;
        const known = looked.get(entry.path);
        if (known) return showPreview(known);           // its row was drawn already
        showPreview({ pending: true, name: entry.name });
        let info = null;
        try {
            info = await foreground(() => api().browse_look(entry.path));
        } catch (e) {
            info = { name: entry.name, path: entry.path, note: 'This file could not be read.' };
        }
        if (mine !== lookToken) return;                 // the user has moved on
        looked.set(entry.path, info);
        showPreview(info);
        if (info && info.picture) paintThumb(entry.path, info.picture);
    }

    /* ---------- row thumbnails, one at a time ---------- */
    function paintThumb(path, picture) {
        const cell = $('browserList').querySelector(`.row-thumb[data-thumb="${CSS.escape(path)}"]`);
        if (cell) cell.innerHTML = `<img src="${picture}" alt="">`;
    }

    async function fillThumbs(mine) {
        const wanted = entries.filter(e => !e.is_dir && !looked.has(e.path)).slice(0, THUMB_ROWS);
        for (const entry of wanted) {
            if (mine !== thumbRun) return;              // folder changed: stop asking
            while (waiting > 0) {                       // someone is waiting; they go first
                await pause(60);
                if (mine !== thumbRun) return;
            }
            let info;
            try {
                // Full size, shrunk by the stylesheet. Asking for a small one would
                // cost a second reading of the same file the moment it is clicked:
                // the picture is cheap, getting at the geometry is not.
                info = await api().browse_look(entry.path);
            } catch (e) {
                return;
            }
            if (mine !== thumbRun) return;
            looked.set(entry.path, info);
            if (info && info.picture) paintThumb(entry.path, info.picture);
        }
    }

    /* ---------- navigating ---------- */
    async function go(path) {
        const mine = ++token;
        thumbRun++;
        atRecent = false;
        looked = new Map();
        $('browserList').innerHTML = '<p class="browse-empty">Reading the folder…</p>';
        let listed;
        try {
            listed = await foreground(() => api().browse_folder(path || '', $('browserShowAll').checked));
        } catch (e) {
            listed = { error: 'That folder could not be read.' };
        }
        if (mine !== token) return;
        if (listed.error) {
            $('browserList').innerHTML = `<p class="browse-empty">${text(listed.error)}</p>`;
            $('browserCrumbs').innerHTML = `<span class="crumb-current">${text(path)}</span>`;
            entries = [];
            selected = -1;
            $('btnBrowserOpen').disabled = true;
            showPreview(null);
            return;
        }
        folder = listed.path;
        parent = listed.parent;
        entries = listed.folders.concat(listed.files);
        selected = -1;
        $('btnBrowserOpen').disabled = true;
        $('btnBrowseUp').disabled = !parent;
        drawCrumbs(listed);
        draw();
        drawPlaces();
        showPreview(null);
        if (!listed.files.length && !listed.folders.length && listed.other_files && !$('browserShowAll').checked) {
            $('browserList').innerHTML = `<p class="browse-empty">No models here.<br>
                <span class="muted">${count(listed.other_files)} other file${listed.other_files === 1 ? '' : 's'} —
                tick “Show every file” to see them.</span></p>`;
        }
        fillThumbs(++thumbRun);
    }

    function drawCrumbs(listed) {
        const crumbs = listed.crumbs || [];
        $('browserCrumbs').innerHTML = crumbs.map((c, i) =>
            i === crumbs.length - 1
                ? `<span class="crumb-current">${text(c.label)}</span>`
                : `<button class="crumb" data-path="${text(c.path)}">${text(c.label)}</button><span class="crumb-sep">›</span>`
        ).join('');
        for (const b of $('browserCrumbs').querySelectorAll('.crumb')) {
            b.addEventListener('click', () => go(b.dataset.path));
        }
    }

    async function showRecent() {
        const mine = ++token;
        thumbRun++;
        atRecent = true;
        looked = new Map();
        $('browserList').innerHTML = '<p class="browse-empty">Looking…</p>';
        let res;
        try {
            res = await foreground(() => api().browse_recent());
        } catch (e) {
            res = { files: [] };
        }
        if (mine !== token) return;
        entries = res.files || [];
        selected = -1;
        folder = '';
        parent = null;
        $('btnBrowserOpen').disabled = true;
        $('btnBrowseUp').disabled = true;
        $('browserCrumbs').innerHTML = '<span class="crumb-current">Recently opened</span>';
        draw();
        drawPlaces();
        showPreview(null);
        if (!entries.length) {
            $('browserList').innerHTML = '<p class="browse-empty">Nothing opened yet.<br><span class="muted">Files you open appear here.</span></p>';
        }
        fillThumbs(++thumbRun);
    }

    /* ---------- opening ---------- */
    function openFile(path) {
        close();
        if (opening) opening(path);
    }

    /* ---------- the dialog itself ---------- */
    const isOpen = () => !$('browser').classList.contains('hidden');

    function close() {
        token++;
        thumbRun++;
        lookToken++;
        $('browser').classList.add('hidden');
    }

    async function open(onPick) {
        opening = onPick;
        if (!api()) return;
        $('browser').classList.remove('hidden');
        $('browserFilter').value = '';
        showPreview(null);
        try {
            // Asked for every time: `start` is wherever the last model came from.
            places = await foreground(() => api().browse_places());
        } catch (e) {
            places = { folders: [], drives: [], start: '' };
        }
        drawPlaces();
        await go(places.start || '');
        $('browserList').focus();
    }

    /* ---------- wiring ---------- */
    document.addEventListener('DOMContentLoaded', () => {
        $('btnBrowserClose').addEventListener('click', close);
        $('btnBrowserCancel').addEventListener('click', close);
        $('btnBrowserOpen').addEventListener('click', () => { if (entries[selected]) openFile(entries[selected].path); });
        $('btnBrowseUp').addEventListener('click', () => { if (parent) go(parent); });
        $('browserShowAll').addEventListener('change', () => { if (!atRecent) go(folder); });
        $('browserFilter').addEventListener('input', () => { selected = -1; draw(); $('btnBrowserOpen').disabled = true; });
        $('browser').addEventListener('click', e => { if (e.target === $('browser')) close(); });

        $('btnBrowseWindows').addEventListener('click', async () => {
            close();
            const path = await api().select_file_dialog();
            if (path && opening) opening(path);
        });

        // Capture, so the dialog answers the arrow keys before the viewport does.
        document.addEventListener('keydown', e => {
            if (!isOpen()) return;
            const typing = e.target === $('browserFilter');
            if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); close(); return; }
            if (e.key === 'ArrowDown') { e.stopPropagation(); e.preventDefault(); move(1); return; }
            if (e.key === 'ArrowUp') { e.stopPropagation(); e.preventDefault(); move(-1); return; }
            if (e.key === 'Enter') { e.stopPropagation(); e.preventDefault(); if (selected >= 0) activate(selected); return; }
            if (e.key === 'Backspace' && !typing) { e.stopPropagation(); e.preventDefault(); $('btnBrowseUp').click(); return; }
            if (!typing && e.key.length === 1 && !e.ctrlKey && !e.altKey) {
                $('browserFilter').focus();             // start typing anywhere to filter
                return;
            }
            e.stopPropagation();                        // nothing else reaches the app behind
        }, true);
    });

    window.meshwrightBrowser = { open, close, isOpen };
})();

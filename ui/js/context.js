/*
 * Right-click on the model.
 *
 * A model made of separate pieces usually needs different things done to different
 * parts of it — the figure kept and the base thinned, two halves fused, an arm moved
 * clear before printing — and until now every one of those went through the panel on
 * the right, which meant finding the piece in a list rather than pointing at it.
 *
 * So the menu is built around what is under the cursor. Right-clicking a piece that
 * is not selected selects it first, because acting on something other than the thing
 * you just pointed at is the one behaviour nobody expects. Shift-right-click adds to
 * the selection instead, matching what shift-click already does.
 *
 * Every entry does something to the pieces named at the top of the menu, and the
 * menu says how many that is, so there is no doubt about the scope of what follows.
 */
(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    const api = () => (window.pywebview && window.pywebview.api) || null;
    const app = () => window.meshwright || {};
    const pieces = () => (app().pieces) || null;

    let menu = null;
    let moving = false;

    const text = s => String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const count = n => Number(n || 0).toLocaleString();

    function close() {
        if (menu) menu.remove();
        menu = null;
    }

    /* ---------- the actions ---------- */
    async function run(label, work, done) {
        close();
        const status = app().setStatus || (() => {});
        status(`${label}…`);
        try {
            const res = await work();
            if (!res || !res.success) {
                status((res && res.error) || `${label} failed`, 'error', 7000);
                return;
            }
            if (res.unchanged) { status('Nothing to change', 'ok', 2500); return; }
            if (app().showModel) app().showModel(res);
            status(done || `${label} done`, 'ok', 3000);
        } catch (e) {
            status(`${label} failed: ${e.message}`, 'error', 7000);
        }
    }

    function startMove(selected) {
        close();
        if (!window.viewer) return;
        const status = app().setStatus || (() => {});
        moving = true;
        const ok = window.viewer.beginMove(selected, async offset => {
            window.viewer.endMove();
            moving = false;
            if (!offset || offset.every(v => Math.abs(v) < 1e-6)) return;
            await run('Moving', () => api().move_pieces([...selected], offset),
                      `Moved ${selected.size} piece${selected.size === 1 ? '' : 's'}`);
        });
        if (!ok) { moving = false; return; }
        status('Drag the arrows to move the selected piece — release to apply, Esc to cancel', 'busy');
    }

    function cancelMove() {
        // The viewer's own state decides, not the flag here. A handle can be put on
        // the model by anything, and an Escape that tidies up only what this file
        // started would leave the other cases stuck with a handle and no way out.
        const attached = !!(window.viewer && window.viewer.moveHandle);
        if (!moving && !attached) return;
        moving = false;
        window.viewer.endMove();
        // The viewport was nudged about while dragging; rebuilding it from the
        // engine is the only honest way back to what the model actually is.
        if (app().refreshViewport) app().refreshViewport();
        (app().setStatus || (() => {}))('Move cancelled', 'ok', 2500);
    }

    /* ---------- building the menu ---------- */
    function entries(selected, piece) {
        const n = selected.size;
        const many = n > 1;
        const total = pieces() ? pieces().all().length : 0;
        const list = [...selected];
        const rows = [];

        if (n) {
            rows.push({ label: 'Move…', hint: 'drag handle', act: () => startMove(selected) });
            rows.push({
                label: `Reduce detail`, submenu: [0.5, 0.25, 0.1].map(keep => ({
                    label: `Keep ${Math.round(keep * 100)}%`,
                    act: () => run(`Reducing ${n} piece${many ? 's' : ''}`,
                                   () => api().optimize_pieces(list, keep),
                                   'Reduced — the rest of the model is untouched'),
                })),
            });
            if (many) {
                rows.push({ label: `Merge into one solid`, hint: `${n} pieces`,
                            act: () => run(`Merging ${n} pieces`, () => api().merge_pieces(list)) });
            }
            rows.push({ separator: true });
            if (total > n) {
                rows.push({ label: 'Keep only these', hint: `drops ${count(total - n)}`,
                            act: () => run('Isolating', () => api().isolate_pieces(list)) });
                rows.push({ label: `Remove`, danger: true, hint: n === 1 ? '' : `${n} pieces`,
                            act: () => run(`Removing ${n} piece${many ? 's' : ''}`,
                                           () => api().remove_shells(list)) });
            }
            rows.push({ separator: true });
        }

        if (total > 1) {
            rows.push({ label: 'Select all pieces', hint: count(total),
                        act: () => { close(); pieces().choose(pieces().all()); } });
        }
        if (n) rows.push({ label: 'Clear selection', act: () => { close(); pieces().choose([]); } });
        rows.push({ label: 'Fit view', hint: 'F', act: () => { close(); window.viewer.fit(); } });
        return rows;
    }

    function build(rows, x, y) {
        close();
        menu = document.createElement('div');
        menu.className = 'ctx-menu';
        for (const row of rows) {
            if (row.separator) {
                menu.appendChild(Object.assign(document.createElement('div'), { className: 'ctx-sep' }));
                continue;
            }
            const item = document.createElement('button');
            item.className = 'ctx-item' + (row.danger ? ' danger' : '') + (row.submenu ? ' has-sub' : '');
            item.innerHTML = `<span>${text(row.label)}</span>`
                + (row.hint ? `<span class="ctx-hint">${text(row.hint)}</span>` : '')
                + (row.submenu ? '<span class="ctx-arrow">&rsaquo;</span>' : '');
            if (row.submenu) {
                const sub = document.createElement('div');
                sub.className = 'ctx-sub';
                for (const child of row.submenu) {
                    const button = document.createElement('button');
                    button.className = 'ctx-item';
                    button.innerHTML = `<span>${text(child.label)}</span>`;
                    button.addEventListener('click', child.act);
                    sub.appendChild(button);
                }
                item.appendChild(sub);
            } else if (row.act) {
                item.addEventListener('click', row.act);
            }
            menu.appendChild(item);
        }
        document.body.appendChild(menu);

        // Keep it on screen: a menu opened near the right or bottom edge flips back
        // over the cursor rather than running off where it cannot be read.
        const box = menu.getBoundingClientRect();
        const left = x + box.width > window.innerWidth - 8 ? Math.max(8, x - box.width) : x;
        const top = y + box.height > window.innerHeight - 8 ? Math.max(8, y - box.height) : y;
        menu.style.left = `${left}px`;
        menu.style.top = `${top}px`;
    }

    function header(selected, piece) {
        if (!selected.size) return null;
        if (selected.size === 1) {
            const info = pieces() && pieces().info([...selected][0]);
            return info
                ? `Piece ${info.index + 1} · ${count(info.faces)} triangles`
                : 'One piece';
        }
        return `${count(selected.size)} pieces selected`;
    }

    /* ---------- wiring ---------- */
    document.addEventListener('DOMContentLoaded', () => {
        const zone = $('dropZone');
        if (!zone) return;

        zone.addEventListener('contextmenu', event => {
            if (!window.viewer || !window.viewer.mesh || !pieces()) return;
            event.preventDefault();
            if (moving) { cancelMove(); return; }

            const piece = window.viewer.pieceAt(event.clientX, event.clientY);
            let selected = pieces().selected();
            if (piece !== null && !selected.has(piece)) {
                // Point at something, get something: acting on a different piece
                // than the one under the cursor is what nobody expects.
                selected = window.viewer.constructor.pickSelection(selected, piece, event.shiftKey);
                pieces().choose([...selected]);
            }
            const rows = entries(selected, piece);
            build(rows, event.clientX, event.clientY);
            const caption = header(selected, piece);
            if (caption && menu) {
                const title = document.createElement('div');
                title.className = 'ctx-title';
                title.textContent = caption;
                menu.insertBefore(title, menu.firstChild);
            }
        });

        window.addEventListener('pointerdown', e => {
            if (menu && !menu.contains(e.target)) close();
        }, true);
        window.addEventListener('blur', close);
        window.addEventListener('resize', close);
        document.addEventListener('keydown', e => {
            if (e.key !== 'Escape') return;
            if (menu) { e.stopPropagation(); close(); return; }
            // cancelMove guards itself on the viewer's state, so no second opinion here.
            if (moving || (window.viewer && window.viewer.moveHandle)) {
                e.stopPropagation();
                cancelMove();
            }
        }, true);
    });

    window.meshwrightContext = { close, cancelMove };
})();

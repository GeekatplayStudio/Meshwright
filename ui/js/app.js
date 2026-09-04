/* Meshwright — Geekatplay Studio */
document.addEventListener('DOMContentLoaded', () => {
    const $ = (id) => document.getElementById(id);
    const api = () => (window.pywebview && window.pywebview.api) || null;

    let current = null; // last analysis

    /* ---------- status ---------- */
    const status = $('status'), statusText = $('statusText');
    let statusTimer = null;
    function setStatus(text, kind = 'busy', autohide = 0) {
        clearTimeout(statusTimer);
        statusText.textContent = text;
        status.className = `status ${kind}`;
        if (autohide) statusTimer = setTimeout(() => status.classList.add('hidden'), autohide);
    }

    /* ---------- helpers ---------- */
    const fmt = (n) => (n == null ? '–' : Number(n).toLocaleString());
    const yesno = (b) => (b ? 'Yes' : 'No');

    function b64ToBuffer(b64) {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return bytes.buffer;
    }

    /* Viewport detail. A dense model is drawn simplified so it appears quickly;
       the mesh itself, the diagnostics and every export use all of it. */
    const detailSlider = $('viewDetail'), detailSeg = $('segDetail'), detailValue = $('valDetail');
    let detailPending = null;

    function showDetail(detail) {
        if (!detail || !detailSlider) return;
        const pct = Math.round(detail.fraction * 100);
        detailSlider.disabled = false;
        detailSlider.value = Math.max(5, Math.min(100, pct));
        detailValue.textContent = `${pct}%`;
        detailSeg.classList.toggle('reduced', !!detail.reduced);

        if (detail.reduced) {
            toast('lod', { kind: 'info', title: `Viewport showing ${pct}% of this model`,
                // Toast bodies are plain text, so no markup here.
                body: `${fmt(detail.faces_total)} triangles is a lot to draw, so the view is simplified `
                    + `to ${fmt(detail.faces_shown)} to keep things quick. Drag the Detail slider up for `
                    + `the full mesh — diagnostics, repair, reduce and export always use every triangle.`,
                ms: 14000 });
        }
    }

    async function applyDetail(percent) {
        if (!api() || !current) return;
        detailSlider.disabled = true;
        try {
            const res = await api().set_preview_detail(percent >= 100 ? 1.0 : percent / 100);
            if (!res || !res.success) { setStatus((res && res.error) || 'Could not change detail', 'error', 5000); return; }
            if (window.viewer) {
                window.viewer.loadGeometry({
                    vertices: b64ToBuffer(res.preview.vertices),
                    faces: b64ToBuffer(res.preview.faces),
                    boundary_edges: b64ToBuffer(res.preview.boundary_edges),
                    uvs: res.preview.uvs ? b64ToBuffer(res.preview.uvs) : null,
                }, res.shell_face_counts || null);
            }
            showDetail(res.detail);
        } catch (e) {
            setStatus(`Could not change detail: ${e.message}`, 'error', 5000);
        } finally {
            detailSlider.disabled = false;
        }
    }

    if (detailSlider) {
        detailSlider.addEventListener('input', () => { detailValue.textContent = `${detailSlider.value}%`; });
        detailSlider.addEventListener('change', () => {
            clearTimeout(detailPending);
            const wanted = +detailSlider.value;
            detailPending = setTimeout(() => applyDetail(wanted), 150);
        });
    }

    function showModel(res) {
        if (res.preview && window.viewer) {
            const t = performance.now();
            const p = res.preview;
            window.viewer.loadGeometry({
                vertices: b64ToBuffer(p.vertices),
                faces: b64ToBuffer(p.faces),
                boundary_edges: b64ToBuffer(p.boundary_edges),
                uvs: p.uvs ? b64ToBuffer(p.uvs) : null,
            }, res.shell_face_counts || null);
            logLine(`Viewport updated in ${Math.round(performance.now() - t)} ms`, 'info');
        }
        $('emptyState').classList.add('hidden');
        hasModel = true;
        if (res.preview && res.preview.detail) showDetail(res.preview.detail);
        if (res.analysis) renderAnalysis(res.analysis);
        renderShells(res.shells || null);
        updateStateUI(res);
        for (const id of ['btnRepair', 'btnReduce', 'btnExport', 'sliderReduce', 'targetInput', 'btnUnwrapUV', 'btnLoadTexture', 'btnOpenUv']) {
            if ($(id)) $(id).disabled = false;
        }
        document.querySelectorAll('.rot').forEach(b => b.disabled = false);
        updateTarget();

        if (res.textures && window.meshwrightTexture) {
            // Cheap: a summary, not the pixels. The texture panel re-fetches the maps
            // itself only when their version has actually moved.
            const summary = res.uv_layout ? { ...res.textures, uv_layout: res.uv_layout } : res.textures;
            window.meshwrightTexture.applyTextureState(summary);
        }
    }

    /* ---------- toasts & progress ----------
       Long operations announce themselves with an estimate, tick a progress bar
       while they run, and report the real elapsed time when they finish. */
    const toastBox = $('toasts');
    const toasts = new Map();          // key -> {el, timer, started, eta, raf}

    function dismissToast(key) {
        const t = toasts.get(key);
        if (!t) return;
        clearTimeout(t.timer);
        cancelAnimationFrame(t.raf);
        t.el.classList.add('leaving');
        setTimeout(() => t.el.remove(), 200);
        toasts.delete(key);
    }

    function toast(key, { kind = 'info', title, body = '', sticky = false, ms = 6000, progress = false } = {}) {
        let t = toasts.get(key);
        if (!t) {
            const el = document.createElement('div');
            el.className = `toast ${kind}`;
            el.innerHTML = `<div class="toast-head">
                    <span class="mark"></span>
                    <span class="toast-title"></span>
                    <span class="toast-time"></span>
                    <button class="toast-x" title="Dismiss">&times;</button>
                </div>
                <div class="toast-body"></div>
                <div class="toast-bar hidden"><i></i></div>`;
            el.querySelector('.toast-x').addEventListener('click', () => dismissToast(key));
            toastBox.appendChild(el);
            t = { el, timer: null, raf: 0 };
            toasts.set(key, t);
        }
        clearTimeout(t.timer);
        cancelAnimationFrame(t.raf);
        t.el.className = `toast ${kind}`;
        t.el.querySelector('.mark').innerHTML =
            kind === 'busy' ? '<span class="spin"></span>' :
            kind === 'ok' ? '<span class="tick">✓</span>' :
            kind === 'error' ? '<span class="bang">!</span>' :
            kind === 'warn' ? '<span class="warnmark">!</span>' : '';
        t.el.querySelector('.toast-title').textContent = title;
        const bodyEl = t.el.querySelector('.toast-body');
        bodyEl.textContent = body;
        bodyEl.classList.toggle('hidden', !body);
        t.el.querySelector('.toast-bar').classList.toggle('hidden', !progress);
        if (!sticky) t.timer = setTimeout(() => dismissToast(key), ms);
        return t;
    }

    /* Runs the elapsed-time counter and the estimate bar for a running job. */
    function runProgress(key, etaSeconds) {
        const t = toasts.get(key);
        if (!t) return;
        t.started = performance.now();
        const timeEl = t.el.querySelector('.toast-time');
        const bar = t.el.querySelector('.toast-bar i');
        const tick = () => {
            if (!toasts.has(key)) return;
            const s = (performance.now() - t.started) / 1000;
            timeEl.textContent = s < 60 ? `${s.toFixed(0)}s` : `${Math.floor(s / 60)}m ${(s % 60).toFixed(0)}s`;
            if (etaSeconds > 0) {
                // asymptotic: approaches but never reaches 100% until it really finishes
                const frac = 1 - Math.exp(-s / etaSeconds);
                bar.style.width = `${Math.min(96, frac * 96).toFixed(1)}%`;
            }
            t.raf = requestAnimationFrame(tick);
        };
        tick();
    }

    /* Called from Python for every long operation. */
    function onProgress(ev) {
        const key = ev.operation || 'job';
        if (ev.state === 'start') {
            const faces = ev.faces ? `${fmt(ev.faces)} faces · ` : '';
            toast(key, { kind: 'busy', title: ev.label, body: `${faces}${ev.eta_text}`, sticky: true, progress: true });
            runProgress(key, ev.eta || 0);
            document.body.classList.add('working');
            logLine(`${ev.label} — ${ev.eta_text}`);
        } else if (ev.state === 'done') {
            const el = toasts.get(key);
            if (el) { cancelAnimationFrame(el.raf); el.el.querySelector('.toast-bar i').style.width = '100%'; }
            toast(key, { kind: 'ok', title: ev.label.replace(/…$/, ''), body: `Finished in ${ev.elapsed}s`, ms: 5000 });
            document.body.classList.remove('working');
        } else if (ev.state === 'error') {
            toast(key, { kind: 'error', title: ev.label, body: ev.error || 'Failed', ms: 10000 });
            document.body.classList.remove('working');
        }
    }

    /* ---------- state / undo / redo ---------- */
    function updateStateUI(res) {
        if (res.state_id != null) $('stateBadge').textContent = `state #${res.state_id}`;
        if ('can_undo' in res) $('btnUndo').disabled = !res.can_undo;
        if ('can_redo' in res) $('btnRedo').disabled = !res.can_redo;
    }
    async function step(which) {
        try {
            const res = await api()[which]();
            if (!res.success) { setStatus(res.error, 'error', 4000); return; }
            $('report').classList.add('hidden');
            showModel(res);
            setStatus(`${which === 'undo' ? 'Undo' : 'Redo'} → state #${res.state_id} (${res.operation})`, 'ok', 3000);
        } catch (e) {
            setStatus(`${which} failed: ${e.message}`, 'error', 4000);
        }
    }
    $('btnUndo').addEventListener('click', () => step('undo'));
    $('btnRedo').addEventListener('click', () => step('redo'));

    /* A mutating result that was rejected by the safety guard. */
    function handleRejected(res, retry) {
        if (!res.rejected) return false;
        openModal('Change rejected',
            `<p>${res.reason.charAt(0).toUpperCase() + res.reason.slice(1)}.</p>
             <p>The previous state (#${res.state_id}) was kept. The result would have been
             <strong>${res.would_be.verdict}</strong> (score ${res.would_be.score}).</p>`,
            [{ label: 'Keep current', primary: true }, { label: 'Apply anyway', action: retry }]);
        setStatus(`Rejected: ${res.reason}`, 'rejected', 6000);
        toast('rejected', { kind: 'warn', title: 'Change rejected — previous state kept',
                            body: res.reason, ms: 12000 });
        return true;
    }

    /* ---------- modal ---------- */
    function openModal(title, bodyHtml, actions) {
        $('modalTitle').textContent = title;
        $('modalBody').innerHTML = bodyHtml;
        const box = $('modalActions');
        box.innerHTML = '';
        for (const act of actions) {
            const b = document.createElement('button');
            b.className = 'btn' + (act.primary ? ' btn-primary' : '');
            b.textContent = act.label;
            b.addEventListener('click', () => { closeModal(); if (act.action) act.action(); });
            box.appendChild(b);
        }
        $('modal').classList.remove('hidden');
    }
    function closeModal() { $('modal').classList.add('hidden'); }
    $('modal').addEventListener('click', e => { if (e.target === $('modal')) closeModal(); });

    /* ---------- crash recovery ---------- */
    function offerRecovery(sessions) {
        const s = sessions[0];
        const name = s.source_file ? s.source_file.split(/[\\/]/).pop() : 'unknown file';
        openModal('Recover previous session?',
            `<p>Meshwright did not close cleanly last time. A snapshot of <strong>${name}</strong>
             (state #${s.last.id}, ${s.last.operation}, ${fmt(s.last.faces)} faces, ${s.last.verdict || ''}) is available.</p>
             ${sessions.length > 1 ? `<p>${sessions.length - 1} older session(s) will be discarded.</p>` : ''}`,
            [{ label: 'Recover', primary: true, action: async () => {
                setStatus('Recovering…');
                const res = await api().recover_session(s.session);
                if (!res.success) { setStatus(res.error, 'error', 5000); return; }
                showModel(res);
                setStatus('Session recovered', 'ok', 3000);
                for (const o of sessions.slice(1)) api().discard_session(o.session);
            } },
            { label: 'Discard', action: () => sessions.forEach(o => api().discard_session(o.session)) }]);
    }

    /* ---------- console ---------- */
    const consoleEl = $('console'), consoleBody = $('consoleBody');
    function logLine(msg, level = 'info') {
        const d = document.createElement('div');
        d.className = level;
        const t = new Date();
        const hh = [t.getHours(), t.getMinutes(), t.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
        d.innerHTML = `<span class="time">${hh}</span>`;
        d.appendChild(document.createTextNode(msg));
        consoleBody.appendChild(d);
        if (consoleBody.children.length > 500) consoleBody.removeChild(consoleBody.firstChild);
        consoleBody.scrollTop = consoleBody.scrollHeight;
        if (level === 'error') openConsole(true);
    }
    function openConsole(open) {
        consoleEl.classList.toggle('open', open);
        $('btnConsole').classList.toggle('active', open);
    }
    $('btnConsole').addEventListener('click', () => openConsole(!consoleEl.classList.contains('open')));
    $('btnConsoleClose').addEventListener('click', () => openConsole(false));
    $('btnConsoleClear').addEventListener('click', () => { consoleBody.innerHTML = ''; });

    /* ---------- shells ---------- */
    let shellSelection = new Set();
    let shellHighlight = true;
    function renderShells(shells) {
        const card = $('cardShells');
        shellSelection = new Set();
        if (!shells || shells.length < 2) { card.classList.add('hidden'); window.viewer.hideShells(); return; }
        card.classList.remove('hidden');
        $('shellCount').textContent = shells.length;
        const list = $('shellList');
        list.innerHTML = '';
        const syncShellButtons = () => {
            $('btnShellsRemove').disabled = shellSelection.size === 0 || shellSelection.size === shells.length;
            $('btnShellsRemove').textContent = shellSelection.size ? `Remove ${shellSelection.size} selected` : 'Remove selected';
            $('btnShellsAll').textContent = shellSelection.size === shells.length ? 'Select none' : 'Select all';
            if (shellHighlight) window.viewer.showShells(shellSelection);
        };
        $('btnShellsAll').onclick = () => {
            const all = shellSelection.size !== shells.length;
            shellSelection = new Set(all ? shells.map(sh => sh.index) : []);
            list.querySelectorAll('li').forEach(li => {
                const cb = li.querySelector('input');
                cb.checked = all;
                li.classList.toggle('selected', all);
            });
            syncShellButtons();
        };
        shells.forEach(sh => {
            const li = document.createElement('li');
            const c = window.viewer.constructor.shellColor(sh.index, false);
            const size = sh.size_mm.map(v => v.toFixed(1)).join('×');
            li.innerHTML = `<input type="checkbox" data-i="${sh.index}"><span class="sw" style="background:#${c.getHexString()}"></span>` +
                `<span>Piece ${sh.index + 1}${sh.watertight ? '' : ' (open)'}</span><span class="meta">${fmt(sh.faces)} tri · ${size} mm</span>`;
            li.addEventListener('click', e => {
                const cb = li.querySelector('input');
                if (e.target !== cb) cb.checked = !cb.checked;
                li.classList.toggle('selected', cb.checked);
                if (cb.checked) shellSelection.add(sh.index); else shellSelection.delete(sh.index);
                syncShellButtons();
            });
            list.appendChild(li);
        });
        syncShellButtons();
        logLine(`${shells.length} separate pieces found — review them in the Separate pieces panel`, 'warn');
    }
    $('btnShellsHighlight').addEventListener('click', e => {
        shellHighlight = !shellHighlight;
        e.currentTarget.classList.toggle('active', shellHighlight);
        if (shellHighlight) window.viewer.showShells(shellSelection); else window.viewer.hideShells();
    });
    $('btnShellsRemove').addEventListener('click', async () => {
        if (!shellSelection.size) return;
        const n = shellSelection.size;
        setStatus(`Removing ${n} piece(s)…`);
        try {
            const res = await api().remove_shells([...shellSelection]);
            if (!res.success) { setStatus(res.error, 'error', 6000); return; }
            showModel(res);
            setStatus(`Removed ${n} piece(s)`, 'ok', 3000);
        } catch (e) {
            setStatus(`Remove failed: ${e.message}`, 'error', 6000);
        }
    });

    /* ---------- about ---------- */
    const LIB_ROLES = {
        'trimesh': 'loading, geometry, analysis, export', 'NumPy': 'array maths', 'SciPy': 'graph / spatial queries',
        'PyMeshLab': 'non-manifold repair, hole closing, decimation', 'Manifold3D': 'manifold solid reconstruction',
        'pymeshfix (MeshFix)': 'hole filling and self-intersection repair', 'fast-simplification': 'fast quadric decimation', 'pyQuadriFlow': 'smart quad retopology (QuadriFlow)',
        'scikit-image': 'marching cubes (voxel remesh)', 'pywebview': 'desktop window', 'mcp': 'MCP server', 'ufbx': 'FBX fallback loader',
    };
    async function showAbout() {
        $('about').classList.remove('hidden');
        if (!api()) return;
        try {
            const info = await api().about_info();
            if (!info.success) return;
            $('aboutVersion').textContent = `v${info.version}`;
            $('aboutThree').textContent = info.three;
            $('aboutPython').textContent = info.python;
            $('aboutPlatform').textContent = info.platform;
            const t = $('aboutLibs');
            t.innerHTML = '';
            for (const [name, ver] of Object.entries(info.libraries)) {
                const tr = document.createElement('tr');
                tr.innerHTML = `<td>${name}</td><td class="${ver ? '' : 'missing'}">${ver || 'not installed'}</td><td>${LIB_ROLES[name] || ''}</td>`;
                t.appendChild(tr);
            }
        } catch { /* static content is still useful offline */ }
    }
    function closeAbout() { $('about').classList.add('hidden'); }
    $('btnAbout').addEventListener('click', showAbout);
    $('btnAboutClose').addEventListener('click', closeAbout);
    $('about').addEventListener('click', e => { if (e.target === $('about')) closeAbout(); });
    document.querySelectorAll('#about a[data-url]').forEach(link => link.addEventListener('click', e => {
        e.preventDefault();
        if (api()) api().open_url(link.dataset.url); else window.open(link.dataset.url, '_blank');
    }));

    /* ---------- ComfyUI installer modal ---------- */
    async function showComfyUIInstaller() {
        if ($('about')) closeAbout();
        let detected = [];
        try {
            if (api() && api().detect_comfyui) {
                const det = await api().detect_comfyui();
                if (det && det.success) detected = det.paths || [];
            }
        } catch { /* ignore */ }

        const defaultPath = detected.length ? detected[0] : '';
        const bodyHtml = `
            <p>Install the <strong>Geekatplay-3D-MeshFix</strong> custom nodes into ComfyUI so you can load, repair, reduce, and preview 3D meshes inside ComfyUI workflows.</p>
            <div style="margin: 16px 0;">
                <label style="display:block;margin-bottom:6px;font-size:13px;font-weight:600;">ComfyUI Directory (or custom_nodes folder):</label>
                <div style="display:flex;gap:8px;margin-bottom:8px;">
                    <input id="comfyPathInput" type="text" style="flex:1;padding:8px 10px;background:rgba(255,255,255,0.06);color:inherit;border:1px solid rgba(255,255,255,0.15);border-radius:4px;font-family:monospace;font-size:12px;" value="${defaultPath.replace(/\\/g, '\\\\')}" placeholder="e.g. D:\\ComfyUI">
                    <button id="btnBrowseComfy" class="btn">Browse…</button>
                </div>
                ${detected.length > 1 ? `
                    <div style="margin-top:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                        Detected installs:
                        ${detected.map(p => `<button class="link btn-preset-comfy" data-p="${p.replace(/"/g, '&quot;')}" style="margin-right:8px;text-decoration:underline;">${p}</button>`).join('')}
                    </div>
                ` : ''}
            </div>
            <p class="hint">Creates <code>Geekatplay-3D-MeshFix</code> in <code>custom_nodes</code> and copies sample workflow <code>mesh_fix_workflow.json</code>.</p>
        `;

        openModal('Install ComfyUI 3D Nodes', bodyHtml, [
            { label: 'Cancel' },
            {
                label: 'Install Nodes',
                primary: true,
                action: async () => {
                    const chosen = ($('comfyPathInput').value || '').trim();
                    if (!chosen) {
                        toast('comfy-err', { kind: 'error', title: 'Path required', body: 'Please specify your ComfyUI path.' });
                        return;
                    }
                    setStatus('Installing ComfyUI custom nodes…');
                    try {
                        const res = await api().install_comfyui_nodes(chosen);
                        if (res && res.success) {
                            setStatus('ComfyUI nodes installed successfully', 'ok', 5000);
                            toast('comfy-ok', {
                                kind: 'ok',
                                title: 'ComfyUI Nodes Installed',
                                body: `Installed to ${res.destination}. Drag & drop mesh_fix_workflow.json into ComfyUI to try it!`,
                                ms: 12000
                            });
                        } else {
                            setStatus(res.error || 'Installation failed', 'error', 6000);
                            toast('comfy-fail', { kind: 'warn', title: 'Installation failed', body: res.error || 'Unknown error' });
                        }
                    } catch (e) {
                        setStatus(`Error: ${e.message}`, 'error', 6000);
                    }
                }
            }
        ]);

        const browseBtn = $('btnBrowseComfy');
        if (browseBtn) {
            browseBtn.addEventListener('click', async () => {
                try {
                    if (api() && api().select_folder_dialog) {
                        const folder = await api().select_folder_dialog();
                        if (folder) $('comfyPathInput').value = folder;
                    }
                } catch { /* ignore */ }
            });
        }
        document.querySelectorAll('.btn-preset-comfy').forEach(b => {
            b.addEventListener('click', () => {
                $('comfyPathInput').value = b.dataset.p;
            });
        });
    }

    $('btnComfyUI').addEventListener('click', showComfyUIInstaller);
    const btnAboutComfy = $('btnAboutComfyUI');
    if (btnAboutComfy) btnAboutComfy.addEventListener('click', showComfyUIInstaller);

    /* ---------- keyboard shortcuts ---------- */
    const SHORTCUTS = [
        ['Ctrl+O', 'Open model'], ['Ctrl+S', 'Export model'], ['Ctrl+Shift+S', 'Save JSON report'],
        ['Ctrl+Z', 'Undo'], ['Ctrl+Y / Ctrl+Shift+Z', 'Redo'], ['Ctrl+R', 'Repair mesh'], ['Ctrl+U', 'Re-analyse'],
        ['Ctrl+A', 'Select all pieces'], ['Delete', 'Remove selected pieces'], ['R', 'Rotation gizmo'],
        ['F', 'Fit view'], ['W', 'Wireframe'], ['E', 'Open-edge highlight'], ['G', 'Build plate'],
        ['1 – 7', 'Top · Front · Right · Iso · Bottom · Back · Left'], ['Esc', 'Clear highlight / close dialog'],
        ['Ctrl+`', 'Console'], ['Ctrl+N', 'Close model / start over'],
        ['Del', 'Remove ticked pieces, or close the model'], ['?', 'This list'],
    ];
    function showHelp() {
        openModal('Keyboard shortcuts',
            `<div class="keys">${SHORTCUTS.map(([k, d]) => `<kbd>${k}</kbd><span>${d}</span>`).join('')}</div>`,
            [{ label: 'Close', primary: true }]);
    }
    $('btnHelp').addEventListener('click', showHelp);
    const VIEWS = ['top', 'front', 'right', 'iso', 'bottom', 'back', 'left'];
    window.addEventListener('keydown', e => {
        const tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' && e.target.type === 'text') return;
        if (tag === 'select' || tag === 'textarea') return;
        const ctrl = e.ctrlKey || e.metaKey;
        const k = e.key.toLowerCase();
        const fire = id => { const b = $(id); if (b && !b.disabled && !b.classList.contains('hidden')) b.click(); };
        if (ctrl && !e.shiftKey && k === 'z') { e.preventDefault(); fire('btnUndo'); }
        else if (ctrl && (k === 'y' || (e.shiftKey && k === 'z'))) { e.preventDefault(); fire('btnRedo'); }
        else if (ctrl && k === 'o') { e.preventDefault(); fire('btnOpen'); }
        else if (ctrl && e.shiftKey && k === 's') { e.preventDefault(); fire('btnReport'); }
        else if (ctrl && k === 's') { e.preventDefault(); fire('btnExport'); }
        else if (ctrl && k === 'r') { e.preventDefault(); fire('btnRepair'); }
        else if (ctrl && k === 'u') { e.preventDefault(); fire('btnReanalyse'); }
        else if (ctrl && k === 'a') { if (!$('cardShells').classList.contains('hidden')) { e.preventDefault(); fire('btnShellsAll'); } }
        else if (ctrl && k === '`') { e.preventDefault(); fire('btnConsole'); }
        else if (ctrl && k === 'n') { e.preventDefault(); requestReset(false); }
        else if (ctrl) { return; }
        else if (k === 'delete' || k === 'backspace') {
            e.preventDefault();
            // Delete removes the ticked pieces when there are any; otherwise it
            // closes the whole model, always after a confirmation.
            if (!$('cardShells').classList.contains('hidden') && !$('btnShellsRemove').disabled) fire('btnShellsRemove');
            else requestReset(true);
        }
        else if (k === 'escape') {
            if (!$('about').classList.contains('hidden')) closeAbout();
            else if (!$('modal').classList.contains('hidden')) closeModal();
            else fire('btnClearHl');
        }
        else if (k === '?') { showHelp(); }
        else if (k === 'f') { fire('btnFit'); }
        else if (k === 'w') { fire('btnWire'); }
        else if (k === 'e') { fire('btnIssues'); }
        else if (k === 'g') { fire('btnGrid'); }
        else if (k === 'r') { fire('btnGizmo'); }
        else if (/^[1-7]$/.test(k)) { window.viewer.setView(VIEWS[+k - 1]); }
    });

    /* ---------- resizable panel ---------- */
    const workspace = document.querySelector('.workspace');
    const MIN_W = 280, MAX_W = 640;
    let panelW = MIN_W + 60;
    try { const s = parseInt(localStorage.getItem('mw.panelW')); if (s >= MIN_W && s <= MAX_W) panelW = s; } catch { /* ignore */ }
    const applyPanel = () => { workspace.style.gridTemplateColumns = `1fr 6px ${panelW}px`; };
    applyPanel();
    $('splitter').addEventListener('pointerdown', e => {
        e.preventDefault();
        const startX = e.clientX, startW = panelW;
        document.body.classList.add('resizing');
        const move = ev => { panelW = Math.min(MAX_W, Math.max(MIN_W, startW + (startX - ev.clientX))); applyPanel(); };
        const up = () => {
            document.body.classList.remove('resizing');
            window.removeEventListener('pointermove', move);
            window.removeEventListener('pointerup', up);
            try { localStorage.setItem('mw.panelW', String(panelW)); } catch { /* ignore */ }
        };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
    });
    $('splitter').addEventListener('dblclick', () => { panelW = MIN_W + 60; applyPanel(); });

    /* Rotate cached issue locations the same way the backend rotated the mesh. */
    function rotateLocations(R, c) {
        if (!current) return;
        const rot = p => [
            R[0][0] * (p[0] - c[0]) + R[0][1] * (p[1] - c[1]) + R[0][2] * (p[2] - c[2]) + c[0],
            R[1][0] * (p[0] - c[0]) + R[1][1] * (p[1] - c[1]) + R[1][2] * (p[2] - c[2]) + c[1],
            R[2][0] * (p[0] - c[0]) + R[2][1] * (p[1] - c[1]) + R[2][2] * (p[2] - c[2]) + c[2],
        ];
        for (const it of current.issues) {
            if (!it.location) continue;
            it.location.points = it.location.points.map(rot);
            it.location.center = rot(it.location.center);
        }
    }

    /* ---------- orientation ----------
       The viewport rotates instantly; the backend bakes the same rotation into
       the mesh afterwards, so no geometry is shipped back and forth. */
    let rotateSync = Promise.resolve();
    window.viewer.onRotate = (matrix) => {
        rotateSync = rotateSync.then(async () => {
            try {
                const res = await api().apply_rotation(matrix);
                if (res && res.success) {
                    if (!res.unchanged) {
                        window.viewer.bakeGroupRotation(res.bounds);
                        rotateLocations(matrix, res.centre);
                    }
                    if (res.stats && current) { current.stats = res.stats; renderStats(res.stats); }
                    updateStateUI(res);
                    document.querySelectorAll('#issueList li').forEach(x => x.classList.remove('active'));
                } else if (res) {
                    setStatus(res.error, 'error', 5000);
                }
            } catch (e) {
                setStatus(`Rotate failed: ${e.message}`, 'error', 5000);
            }
        });
    };
    document.querySelectorAll('.rot').forEach(b =>
        b.addEventListener('click', () => window.viewer.rotateModel(b.dataset.axis, 90)));
    $('btnGizmo').addEventListener('click', e =>
        e.currentTarget.classList.toggle('active', window.viewer.attachGizmo(!window.viewer.gizmoOn)));

    /* ---------- analysis panel ---------- */
    function renderAnalysis(a) {
        current = a;
        const s = a.stats;
        $('fileName').textContent = s.filename || '';

        // score ring
        const ring = $('scoreRing');
        const color = a.score >= 80 ? 'var(--good)' : a.score >= 50 ? 'var(--warn)' : 'var(--bad)';
        ring.style.setProperty('--pct', a.score);
        ring.style.setProperty('--ring-color', color);
        $('scoreValue').textContent = a.score;
        $('verdict').textContent = a.verdict;
        const crit = a.issues.filter(i => i.severity === 'critical').length;
        const warn = a.issues.filter(i => i.severity === 'warning').length;
        $('verdictSub').textContent = a.issues.length === 0
            ? 'Closed, consistently oriented solid. Ready to slice.'
            : `${crit} critical · ${warn} warnings · ${a.issues.length - crit - warn} notes`;

        // issues
        const list = $('issueList');
        list.innerHTML = '';
        $('issueCount').textContent = a.issues.length || '';
        if (a.issues.length === 0) {
            list.innerHTML = '<li class="ok"><div><div class="t">No problems found</div><div class="d">Mesh is watertight, manifold and correctly oriented.</div></div></li>';
        }
        for (const it of a.issues) {
            const li = document.createElement('li');
            li.className = it.severity;
            const loc = it.location;
            const where = loc ? `<div class="loc">${loc.total === 1 ? '1 spot' : fmt(loc.total) + ' spots'} · region ≈ ${loc.extent} mm</div>` : '';
            li.innerHTML = `<div><div class="t">${it.title}</div><div class="d">${it.detail}</div>${where}</div>`;
            if (loc) {
                li.classList.add('locatable');
                li.addEventListener('click', () => {
                    const on = li.classList.contains('active');
                    list.querySelectorAll('li').forEach(x => x.classList.remove('active'));
                    if (on) { window.viewer.clearHighlight(); return; }
                    li.classList.add('active');
                    window.viewer.highlight(loc);
                    logLine(`Located ${it.title}: ${loc.total} spot(s) around (${loc.center.join(', ')}) mm`);
                });
            }
            list.appendChild(li);
        }
        $('btnSlivers').classList.toggle('hidden', !a.issues.some(i => i.id === 'slivers'));
        $('btnReport').disabled = false;
        $('btnReanalyse').disabled = false;
        $('btnRevert').disabled = false;

        renderStats(s);
    }

    function renderStats(s) {
        $('sFaces').textContent = fmt(s.face_count);
        $('sVerts').textContent = fmt(s.vertex_count);
        $('sEdges').textContent = fmt(s.edge_count);
        $('sBodies').textContent = fmt(s.body_count);
        $('sDims').textContent = s.dimensions_mm ? `${s.dimensions_mm[0]} × ${s.dimensions_mm[1]} × ${s.dimensions_mm[2]} mm` : '–';
        $('sVolume').textContent = s.is_watertight ? `${s.volume_cm3} cm³` : 'n/a (open)';
        $('sArea').textContent = `${s.surface_area_cm2} cm²`;
        flag('sWater', s.is_watertight, yesno(s.is_watertight));
        flag('sWinding', s.is_winding_consistent, s.is_winding_consistent ? 'Consistent' : 'Mixed');
        flag('sBoundary', s.boundary_edges === 0, fmt(s.boundary_edges));
        flag('sNonMan', s.nonmanifold_edges === 0, fmt(s.nonmanifold_edges));
        $('sGenus').textContent = s.genus == null ? '–' : s.genus;
    }

    function flag(id, good, text) {
        const el = $(id);
        el.textContent = text;
        el.className = good ? 'good' : 'bad';
    }

    function renderReport(report) {
        const fixList = $('fixList');
        fixList.innerHTML = '';
        if (!report.fixes || report.fixes.length === 0) {
            fixList.innerHTML = '<li class="none">Nothing needed changing.</li>';
        }
        for (const f of report.fixes || []) {
            const li = document.createElement('li');
            li.innerHTML = `<strong>${f.stage}</strong> — ${f.description}`;
            fixList.appendChild(li);
        }
        if (report.passes > 1) {
            const li = document.createElement('li');
            li.className = 'none';
            li.textContent = `Repair ran ${report.passes} passes until the mesh stopped changing.`;
            fixList.appendChild(li);
        }
        if (report.after && report.after.issues.length) {
            const li = document.createElement('li');
            li.className = 'warn';
            li.textContent = `${report.after.issues.length} issue(s) remain — see Diagnostics (verified on the repaired mesh).`;
            fixList.appendChild(li);
        }
        const table = $('changeTable');
        table.innerHTML = '';
        const show = (v) => (typeof v === 'boolean' ? yesno(v) : fmt(v));
        for (const c of report.changes || []) {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td>${c.label}</td><td>${show(c.before)} →</td><td class="${c.improved ? 'good' : 'neutral'}">${show(c.after)}</td>`;
            table.appendChild(tr);
        }
        if (!report.changes || report.changes.length === 0) {
            table.innerHTML = '<tr><td class="muted">No measurable change.</td></tr>';
        }
        $('report').classList.remove('hidden');
    }

    /* ---------- loading ---------- */
    async function loadFile(path) {
        if (!api()) { setStatus('Desktop bridge not available', 'error', 4000); return; }
        const name = path.split(/[\\/]/).pop();
        setStatus(`Loading ${name}…`);
        openConsole(true);
        $('report').classList.add('hidden');
        try {
            const res = await api().load_model_file(path);
            if (!res.success) { setStatus(res.error, 'error', 6000); return; }
            showModel(res);
            setStatus(`Analysed ${name}`, 'ok', 2500);
        } catch (e) {
            setStatus(`Failed: ${e.message}`, 'error', 6000);
        }
    }

    $('btnOpen').addEventListener('click', async () => {
        if (!api()) return;
        const path = await api().select_file_dialog();
        if (path) loadFile(path);
    });

    /* ---------- close the model / start over ---------- */
    let hasModel = false;

    function clearWorkspaceUI() {
        current = null;
        hasModel = false;

        if (window.viewer) window.viewer.reset();
        if (window.meshwrightTexture) window.meshwrightTexture.resetTextureState();

        $('emptyState').classList.remove('hidden');
        $('report').classList.add('hidden');
        $('cardShells').classList.add('hidden');
        $('shellList').innerHTML = '';
        $('shellCount').textContent = '';
        $('fileName').textContent = '';
        $('stateBadge').textContent = '';
        $('reduceResult').textContent = '';
        $('issueCount').textContent = '';
        $('issueList').innerHTML = '<li class="muted">—</li>';
        $('btnSlivers').classList.add('hidden');

        const ring = $('scoreRing');
        ring.style.setProperty('--pct', 0);
        ring.style.setProperty('--ring-color', 'var(--line)');
        $('scoreValue').textContent = '–';
        $('verdict').textContent = 'No model loaded';
        $('verdictSub').textContent = 'Open a file to run diagnostics. '
            + 'Drag to orbit, scroll to zoom, right-drag to pan.';
        for (const id of ['sFaces', 'sVerts', 'sEdges', 'sBodies', 'sDims', 'sVolume',
                          'sArea', 'sWater', 'sWinding', 'sBoundary', 'sNonMan', 'sGenus']) {
            if ($(id)) $(id).textContent = '–';
        }

        for (const id of ['btnRepair', 'btnReduce', 'btnExport', 'sliderReduce', 'targetInput',
                          'btnUndo', 'btnRedo', 'btnRevert', 'btnReanalyse', 'btnReport',
                          'btnShellsRemove', 'viewDetail']) {
            if ($(id)) $(id).disabled = true;
        }
        if (detailSeg) detailSeg.classList.remove('reduced');
        if (detailValue) detailValue.textContent = '100%';
        if (detailSlider) detailSlider.value = 100;
        document.querySelectorAll('.rot').forEach(b => b.disabled = true);
        status.classList.add('hidden');
    }

    async function resetWorkspace() {
        if (api()) {
            try {
                const res = await api().close_model();
                if (res && res.success === false) {
                    setStatus(res.error || 'Could not close the model', 'error', 5000);
                    return;
                }
            } catch (e) {
                setStatus(`Could not close the model: ${e.message}`, 'error', 5000);
                return;
            }
        }
        clearWorkspaceUI();
        setStatus('Workspace cleared — open a model to start', 'ok', 3000);
    }

    /* Confirm before throwing work away; an unmodified model (state #1, straight
       from disk) costs nothing to reopen, so that case goes straight through. */
    function requestReset(force) {
        if (!hasModel) { clearWorkspaceUI(); return; }
        const modified = current && $('stateBadge').textContent !== 'state #1';
        if (!force && !modified) { resetWorkspace(); return; }
        const name = $('fileName').textContent || 'the current model';
        openModal('Close the model?',
            `<p><strong>${name}</strong> will be removed from the workspace.</p>
             <p>${modified ? 'Unsaved changes and the whole undo history are discarded.'
                           : 'Nothing has been changed, so nothing is lost.'}
             Export first if you want to keep the result.</p>`,
            [{ label: 'Keep working' }, { label: 'Close model', primary: true, action: resetWorkspace }]);
    }

    $('btnNew').addEventListener('click', () => requestReset(false));

    /* The demo model is built in memory by Python — nothing is downloaded and
       no file is written. It exists so a fresh install can be tried at once. */
    $('btnDemo').addEventListener('click', async () => {
        if (!api()) { setStatus('Desktop bridge not available', 'error', 4000); return; }
        setStatus('Building the demo model…');
        openConsole(true);
        $('report').classList.add('hidden');
        try {
            const res = await api().load_demo_model();
            if (!res.success) { setStatus(res.error, 'error', 6000); return; }
            showModel(res);
            setStatus('Demo model loaded — press Repair to see it fixed', 'ok', 5000);
            toast('demo', { kind: 'info', title: 'Built-in demo model',
                body: 'A sphere with a hole, a patch of flipped faces and a loose second piece. ' +
                      'This is a test object, not a printable part.', ms: 9000 });
        } catch (e) {
            setStatus(`Failed: ${e.message}`, 'error', 6000);
        }
    });

    const zone = $('dropZone'), overlay = $('dropOverlay');
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); overlay.classList.remove('hidden'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); overlay.classList.add('hidden'); }));
    // The actual file path arrives via the Python-side drop handler (see app.py),
    // which calls window.meshwright.load(path).
    window.meshwright = {
        load: loadFile, log: logLine, offerRecovery, progress: onProgress, toast, showModel,
        confirm: (title, bodyHtml, confirmLabel, onConfirm) => openModal(title, bodyHtml,
            [{ label: 'Cancel' }, { label: confirmLabel, primary: true, action: onConfirm }]),
    };

    /* ---------- repair ---------- */
    $('btnRepair').addEventListener('click', async () => {
        setStatus('Repairing…');
        $('btnRepair').disabled = true;
        try {
            const res = await api().auto_fix_mesh($('chkStrict').checked, false);
            if (handleRejected(res, () => forceRepair())) return;
            if (!res.success) { setStatus(res.error, 'error', 6000); return; }
            showModel(res);
            renderReport(res.report);
            const ok = res.analysis && res.analysis.stats.is_watertight;
            const fixes = (res.report.fixes || []).length;
            toast('repair-result', { kind: ok ? 'ok' : 'warn',
                title: ok ? 'Repair complete — watertight' : 'Repair finished — open edges remain',
                body: fixes ? `${fixes} fix(es) applied over ${res.report.passes} pass(es)` : 'Nothing needed changing',
                ms: 12000 });
            setStatus(ok ? 'Repair complete — mesh is watertight' : 'Repair finished — open edges remain', ok ? 'ok' : 'error', 4000);
        } catch (e) {
            setStatus(`Repair failed: ${e.message}`, 'error', 6000);
        } finally {
            $('btnRepair').disabled = false;
        }
    });

    /* ---------- re-analyse / revert ---------- */
    $('btnReanalyse').addEventListener('click', async () => {
        setStatus('Re-analysing current mesh…');
        try {
            const res = await api().analyze_current();
            if (!res.success) { setStatus(res.error, 'error', 5000); return; }
            renderAnalysis(res.analysis);
            setStatus(`Re-analysed: ${res.analysis.verdict}`, res.analysis.score >= 80 ? 'ok' : 'error', 4000);
        } catch (e) {
            setStatus(`Analysis failed: ${e.message}`, 'error', 5000);
        }
    });
    $('btnRevert').addEventListener('click', () => openModal('Revert to original?',
        '<p>All changes in this session will be discarded and the file reloaded as it was opened. You can still undo this.</p>',
        [{ label: 'Cancel' }, { label: 'Revert', primary: true, action: doRevert }]));
    async function doRevert() {
        setStatus('Reverting to the file as loaded…');
        try {
            const res = await api().revert_to_original();
            if (!res.success) { setStatus(res.error, 'error', 5000); return; }
            $('report').classList.add('hidden');
            showModel(res);
            setStatus('Reverted to original', 'ok', 3000);
        } catch (e) {
            setStatus(`Revert failed: ${e.message}`, 'error', 5000);
        }
    }

    async function forceRepair() {
        setStatus('Repairing (forced)…');
        const res = await api().auto_fix_mesh($('chkStrict').checked, true);
        if (!res.success) { setStatus(res.error, 'error', 6000); return; }
        showModel(res);
        renderReport(res.report);
        setStatus('Repair applied (forced)', 'ok', 4000);
    }

    /* ---------- slivers ---------- */
    $('btnSlivers').addEventListener('click', async () => {
        setStatus('Fixing sliver triangles…');
        $('btnSlivers').disabled = true;
        try {
            const res = await api().fix_sliver_faces(1.0, false);
            if (handleRejected(res, async () => { const r = await api().fix_sliver_faces(1.0, true); if (r.success) showModel(r); })) return;
            if (!res.success) { setStatus(res.error, 'error', 6000); return; }
            showModel(res);
            const i = res.info;
            const left = i.after ? ` · ${i.after} left in place (removing them would tear the surface)` : '';
            toast('slivers', { kind: i.after ? 'warn' : 'ok',
                title: `Slivers ${i.before} → ${i.after}`,
                body: `merged ${i.collapsed}, flipped ${i.flipped}${left}`, ms: 12000 });
            setStatus(`Slivers ${i.before} → ${i.after} (merged ${i.collapsed}, flipped ${i.flipped})`, i.after ? 'rejected' : 'ok', 5000);
        } catch (e) {
            setStatus(`Sliver fix failed: ${e.message}`, 'error', 6000);
        } finally {
            $('btnSlivers').disabled = false;
        }
    });

    /* ---------- report ---------- */
    $('btnReport').addEventListener('click', async () => {
        try {
            const res = await api().export_report();
            if (res.canceled) return;
            if (!res.success) { setStatus(res.error, 'error', 5000); return; }
            setStatus(`Report saved: ${res.path.split(/[\\/]/).pop()}`, 'ok', 4000);
        } catch (e) {
            setStatus(`Report failed: ${e.message}`, 'error', 5000);
        }
    });

    /* ---------- views / compass ---------- */
    document.querySelectorAll('.views button[data-view]').forEach(b => b.addEventListener('click', () => window.viewer.setView(b.dataset.view)));
    $('btnClearHl').addEventListener('click', () => {
        window.viewer.clearHighlight();
        document.querySelectorAll('#issueList li').forEach(x => x.classList.remove('active'));
    });
    window.viewer.compassCtx = $('compass').getContext('2d');

    /* ---------- reduce (decimate / smart retopo) ---------- */
    const slider = $('sliderReduce');
    const targetInput = $('targetInput');
    let reduceMode = 'quadriflow';

    function currentFaces() { return current ? current.stats.face_count : 0; }
    function setTargetFromSlider() {
        if (!current) return;
        const keep = 1 - slider.value / 100;
        const t = Math.max(20, Math.round(currentFaces() * keep));
        targetInput.value = t;
        $('reduceVal').textContent = `${slider.value}%`;
    }
    function setTargetFaces(n) {
        if (!current) return;
        const t = Math.max(20, Math.min(currentFaces(), Math.round(n)));
        targetInput.value = t;
        const pct = Math.max(5, Math.min(99.5, 100 * (1 - t / currentFaces())));
        slider.value = pct.toFixed(1);
        $('reduceVal').textContent = `${(+slider.value).toFixed(1)}%`;
    }
    function updateTarget() {
        if (!current) return;
        targetInput.max = currentFaces();
        setTargetFromSlider();
    }
    slider.addEventListener('input', setTargetFromSlider);
    targetInput.addEventListener('change', () => setTargetFaces(+targetInput.value || 20));
    document.querySelectorAll('.presets button').forEach(b => b.addEventListener('click', () => {
        if (!current) return;
        setTargetFaces(+b.dataset.faces);
    }));
    document.querySelectorAll('#reduceMode button').forEach(b => b.addEventListener('click', () => {
        document.querySelectorAll('#reduceMode button').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        reduceMode = b.dataset.reduce;
        $('rowSharp').style.visibility = reduceMode === 'quadriflow' ? 'visible' : 'hidden';
    }));

    $('btnReduce').addEventListener('click', async () => {
        if (!current) return;
        const target = Math.max(20, +targetInput.value || 20);
        const label = { quadriflow: 'Smart retopology', quadric: 'Decimating', isotropic: 'Uniform remeshing' }[reduceMode];
        setStatus(`${label} to ${fmt(target)} faces…`);
        $('reduceResult').textContent = '';
        $('btnReduce').disabled = true;
        try {
            const res = await api().retopologize(target, reduceMode, $('chkSharp').checked, true);
            if (!res.success) { setStatus(res.error, 'error', 6000); return; }
            showModel(res);
            const i = res.info, d = i.deviation;
            toast('reduce-result', { kind: d.relative_pct < 5 ? 'ok' : 'warn',
                title: `Reduced to ${fmt(i.final_faces)} faces`,
                body: `${i.reduction_percentage}% fewer · ${i.method_used} · deviation max ${d.max_mm} mm (${d.relative_pct}%)`,
                ms: 12000 });
            const cls = d.relative_pct < 2 ? 'dev-ok' : 'dev-warn';
            $('reduceResult').innerHTML = `${fmt(i.initial_faces)} → <strong>${fmt(i.final_faces)}</strong> faces (${i.method_used}) · ` +
                `<span class="${cls}">deviation max ${d.max_mm} mm (${d.relative_pct}% of size), mean ${d.mean_mm} mm</span>`;
            setStatus(`Reduced ${i.reduction_percentage}% → ${fmt(i.final_faces)} faces`, 'ok', 4000);
        } catch (e) {
            setStatus(`Reduction failed: ${e.message}`, 'error', 6000);
        } finally {
            $('btnReduce').disabled = false;
        }
    });

    /* A file that is not a closed solid slices as a single-wall shell with no
       infill, which is invisible until the print is half done — so say it here. */
    function showExportWarnings(result) {
        const warnings = result.warnings || [];
        if (warnings.length === 0) return;   // Python already logged them to the console
        toast('export-warning', {
            kind: 'warn',
            title: result.is_solid ? 'Saved — check this before printing'
                                   : 'Saved, but this is not a printable solid',
            body: warnings.join(' '),
            sticky: true
        });
    }

    /* ---------- export ---------- */
    $('btnExport').addEventListener('click', async () => {
        const format = $('selExportFormat').value;
        setStatus(`Exporting ${format.toUpperCase()}…`);
        $('btnExport').disabled = true;
        try {
            const res = await api().export_model_file(format, $('selUnit').value, $('chkAlign').checked);
            if (res.canceled) { status.classList.add('hidden'); return; }
            if (!res.success) { setStatus(res.error, 'error', 6000); return; }
            setStatus(`Saved ${res.result.filename} (${res.result.file_size_mb} MB)`, 'ok', 4000);
            showExportWarnings(res.result);
        } catch (e) {
            setStatus(`Export failed: ${e.message}`, 'error', 6000);
        } finally {
            $('btnExport').disabled = false;
        }
    });

    /* ---------- view ---------- */
    document.querySelectorAll('.viewport-tools button[data-mode]').forEach(b => b.addEventListener('click', () => {
        document.querySelectorAll('.viewport-tools button[data-mode]').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        window.viewer.setMode(b.dataset.mode);
    }));
    $('btnWire').addEventListener('click', e => {
        const heavy = current && current.stats.face_count > 800000;
        if (heavy && !window.viewer.showWire) setStatus('Building wireframe for a very dense mesh…', 'busy');
        const on = window.viewer.toggleWire();
        e.currentTarget.classList.toggle('active', on);
        if (heavy) {
            if (on) setStatus(`Wireframe on ${fmt(current.stats.face_count)} faces — this slows the viewport`, 'rejected', 5000);
            else status.classList.add('hidden');
        }
    });
    $('btnIssues').addEventListener('click', e => e.currentTarget.classList.toggle('active', window.viewer.toggleEdges()));
    $('btnGrid').addEventListener('click', e => e.currentTarget.classList.toggle('active', window.viewer.toggleGrid()));
    $('btnFit').addEventListener('click', () => window.viewer.fit());

    const light = () => window.viewer.setLight(+$('lightAz').value, +$('lightEl').value, +$('lightPow').value);
    ['lightAz', 'lightEl', 'lightPow'].forEach(id => $(id).addEventListener('input', light));
});

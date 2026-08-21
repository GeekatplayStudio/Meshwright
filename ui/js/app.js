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

    function showModel(res) {
        if (res.preview && window.viewer) {
            const t = performance.now();
            const p = res.preview;
            window.viewer.loadGeometry({
                vertices: b64ToBuffer(p.vertices),
                faces: b64ToBuffer(p.faces),
                boundary_edges: b64ToBuffer(p.boundary_edges),
            }, res.shell_face_counts || null);
            logLine(`Viewport updated in ${Math.round(performance.now() - t)} ms`, 'info');
        }
        $('emptyState').classList.add('hidden');
        if (res.analysis) renderAnalysis(res.analysis);
        renderShells(res.shells || null);
        updateStateUI(res);
        for (const id of ['btnRepair', 'btnReduce', 'btnExport', 'sliderReduce', 'targetInput']) $(id).disabled = false;
        document.querySelectorAll('.rot').forEach(b => b.disabled = false);
        updateTarget();
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

    /* ---------- keyboard shortcuts ---------- */
    const SHORTCUTS = [
        ['Ctrl+O', 'Open model'], ['Ctrl+S', 'Export STL'], ['Ctrl+Shift+S', 'Save JSON report'],
        ['Ctrl+Z', 'Undo'], ['Ctrl+Y / Ctrl+Shift+Z', 'Redo'], ['Ctrl+R', 'Repair mesh'], ['Ctrl+U', 'Re-analyse'],
        ['Ctrl+A', 'Select all pieces'], ['Delete', 'Remove selected pieces'], ['R', 'Rotation gizmo'],
        ['F', 'Fit view'], ['W', 'Wireframe'], ['E', 'Open-edge highlight'], ['G', 'Build plate'],
        ['1 – 7', 'Top · Front · Right · Iso · Bottom · Back · Left'], ['Esc', 'Clear highlight / close dialog'],
        ['Ctrl+`', 'Console'], ['?', 'This list'],
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
        else if (ctrl) { return; }
        else if (k === 'delete' || k === 'backspace') { if (!$('cardShells').classList.contains('hidden')) { e.preventDefault(); fire('btnShellsRemove'); } }
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

    const zone = $('dropZone'), overlay = $('dropOverlay');
    ['dragenter', 'dragover'].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); overlay.classList.remove('hidden'); }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); overlay.classList.add('hidden'); }));
    // The actual file path arrives via the Python-side drop handler (see app.py),
    // which calls window.meshwright.load(path).
    window.meshwright = { load: loadFile, log: logLine, offerRecovery };

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
            setStatus(`Slivers ${i.before} → ${i.after} (merged ${i.collapsed}, flipped ${i.flipped})`, i.after ? 'error' : 'ok', 5000);
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
        $('btnReduce').disabled = true;
        try {
            const res = await api().retopologize(target, reduceMode, $('chkSharp').checked, true);
            if (!res.success) { setStatus(res.error, 'error', 6000); return; }
            showModel(res);
            const i = res.info, d = i.deviation;
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

    /* ---------- export ---------- */
    $('btnExport').addEventListener('click', async () => {
        setStatus('Exporting STL…');
        $('btnExport').disabled = true;
        try {
            const res = await api().export_stl_file($('selUnit').value, $('chkAlign').checked);
            if (res.canceled) { status.classList.add('hidden'); return; }
            if (!res.success) { setStatus(res.error, 'error', 6000); return; }
            setStatus(`Saved ${res.result.filename} (${res.result.file_size_mb} MB)`, 'ok', 4000);
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

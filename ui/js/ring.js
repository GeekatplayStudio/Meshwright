/*
 * The jewellery panel: measuring a ring, and putting it right.
 *
 * Shut by default. Almost nobody printing miniatures wants ring sizes in their way,
 * and the few who do want them every time — so the card remembers being opened and
 * stays that way.
 *
 * The work is split across two programs and the panel says so plainly, because when
 * Blender is missing the person needs to know that is what is wrong. Meshwright
 * measures; Blender cuts the bore and takes the edges off. Nothing is claimed that
 * was not measured afterwards: the size reported is read back off the corrected
 * ring, not the size that was asked for.
 */
(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    const api = () => (window.pywebview && window.pywebview.api) || null;
    const app = () => window.meshwright || {};
    const REMEMBER = 'meshwright.ring.open';

    let blenderReady = false;

    const text = s => String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const mm = v => (v == null ? '–' : `${Number(v).toFixed(2)} mm`);

    /* ---------- Blender ---------- */
    async function refreshBlender() {
        if (!api()) return;
        let state;
        try {
            state = await api().blender_status();
        } catch (e) {
            return;
        }
        const label = $('ringBlenderState');
        blenderReady = !!(state && state.found);
        if (blenderReady) {
            label.textContent = `Blender: ${state.path.split(/[\\/]/).pop()}`;
            label.title = state.path;
            label.className = 'ring-blender-state ok';
        } else {
            label.textContent = 'Blender not found — fixing needs it';
            label.title = (state && state.reason) || '';
            label.className = 'ring-blender-state missing';
        }
        updateButtons();
    }

    function updateButtons() {
        const has = !!app().hasModel;
        $('btnRingMeasure').disabled = !has;
        $('btnRingFix').disabled = !has || !blenderReady;
        $('btnRingFix').title = blenderReady ? '' : 'This needs Blender. Use “Find Blender…”.';
    }

    /* ---------- showing what was measured ---------- */
    function showMeasurement(ring, into, scalePercent) {
        // When the model has been scaled for shrinkage, what is on screen is the wax,
        // not the ring. Reporting the wax's size as the finger size is how somebody
        // ends up with a ring half a size too big and no idea why.
        const scale = Number(scalePercent || 100);
        const cast = Math.abs(scale - 100) > 0.001
            ? { iso: +(ring.circumference_mm / (scale / 100)).toFixed(1),
                us: +(7 + (ring.circumference_mm / (scale / 100) - 54.4) / 2.55).toFixed(2) }
            : null;
        const rows = [
            ['Bore', `${mm(ring.bore_min_mm)} – ${mm(ring.bore_max_mm)}`],
            [cast ? 'Size as printed' : 'Size', `ISO ${ring.iso_size} · US ${ring.us_size}`],
            ['Band width', mm(ring.band_width_mm)],
            ['Outer', mm(ring.outer_diameter_mm)],
            ['Thinnest wall', ring.thinnest_wall_mm == null
                ? 'not measurable on this mesh' : mm(ring.thinnest_wall_mm)],
        ];
        if (cast) rows.splice(2, 0, ['Size once cast', `ISO ${cast.iso} · US ${cast.us}`]);
        const trouble = [];
        if (ring.out_of_round) {
            trouble.push(`The bore is <strong>${mm(ring.ovality_mm)} out of round</strong>. `
                + `A finger needs a circle; this is an oval.`);
        }
        if (ring.too_thin) {
            trouble.push(`The band is <strong>${mm(ring.thinnest_wall_mm)}</strong> at its thinnest. `
                + `A ring band wants at least ${mm(ring.rules && ring.rules.band_min_mm || 1.0)} `
                + `to fill in casting and to survive being worn.`);
        }
        if (ring.thinnest_wall_mm == null) {
            trouble.push('Wall thickness could not be measured: the mesh is not a closed, '
                + 'consistently wound solid. Repair it first and it becomes exact.');
        }

        into.innerHTML =
            `<div class="ring-verdict ${trouble.length ? 'warn' : 'good'}">`
            + (trouble.length ? 'Not ready to cast' : 'Measures like a wearable ring') + '</div>'
            + `<table class="ring-facts">${rows.map(([k, v]) =>
                `<tr><td>${k}</td><td>${v}</td></tr>`).join('')}</table>`
            + trouble.map(t => `<div class="ring-note">${t}</div>`).join('');
    }

    async function measure() {
        const button = $('btnRingMeasure');
        button.disabled = true;
        const was = button.textContent;
        button.textContent = 'Measuring…';
        try {
            const res = await api().measure_ring();
            if (!res || !res.success) {
                $('ringReport').innerHTML = `<div class="ring-note">${text((res && res.error)
                    || 'This could not be measured.')}</div>`;
                return;
            }
            showMeasurement(res.ring, $('ringReport'), 100);
            // Offer back the size it already is, so "just make it round" is one click.
            if ($('ringSizeSystem').value === 'us') $('ringSize').value = res.ring.us_size;
            else $('ringSize').value = res.ring.iso_size;
        } catch (e) {
            $('ringReport').innerHTML = `<div class="ring-note">${text(e.message)}</div>`;
        } finally {
            button.textContent = was;
            updateButtons();
        }
    }

    async function fix() {
        const button = $('btnRingFix');
        button.disabled = true;
        const was = button.textContent;
        button.textContent = 'Fixing…';
        const system = $('ringSizeSystem').value;
        const size = parseFloat($('ringSize').value) || 0;
        try {
            const res = await api().fix_ring(
                system === 'iso' ? size : 0,
                system === 'us' ? size : 0,
                $('ringComfort').checked,
                parseFloat($('ringBevel').value) || 0,
                parseFloat($('ringMinWall').value) || 0,
                parseFloat($('ringScale').value) || 100,
                $('ringRepairFirst').checked);
            if (!res || !res.success) {
                $('ringResult').innerHTML = `<div class="ring-note">${text((res && res.error)
                    || 'The ring could not be fixed.')}</div>`;
                return;
            }
            if (app().showModel) app().showModel(res);
            showMeasurement(res.ring, $('ringResult'), parseFloat($('ringScale').value) || 100);

            const asked = system === 'iso' ? size : null;
            const got = res.ring;
            const extra = [];
            if (got.out_of_round) {
                extra.push(`Still ${mm(got.ovality_mm)} out of round. Cutting a bore can only take `
                    + `metal away, and this one was already wider than the size asked for in places. `
                    + `<strong>ISO ${got.round_from_iso} (US ${got.round_from_us})</strong> or larger `
                    + `comes out truly round.`);
            }
            const scale = parseFloat($('ringScale').value) || 100;
            if (Math.abs(scale - 100) > 0.001) {
                const finished = (got.circumference_mm / (scale / 100)).toFixed(1);
                extra.push(`The model on screen is the <strong>wax</strong>, printed at ${scale}% `
                    + `and measuring ISO ${got.iso_size}. Allowing for what the wax and the metal `
                    + `lose, the finished ring should come out at <strong>ISO ${finished}</strong>. `
                    + `That percentage is yours to dial in with a test cast.`);
            }
            if (extra.length) {
                $('ringResult').innerHTML += extra.map(t =>
                    `<div class="ring-note">${t}</div>`).join('');
            }
            if (app().setStatus) app().setStatus(`Ring corrected to ISO ${got.iso_size}`, 'ok', 4000);
        } catch (e) {
            $('ringResult').innerHTML = `<div class="ring-note">${text(e.message)}</div>`;
        } finally {
            button.textContent = was;
            updateButtons();
        }
    }

    /* ---------- wiring ---------- */
    document.addEventListener('DOMContentLoaded', () => {
        const fold = $('ringFold');
        try {
            if (localStorage.getItem(REMEMBER) === '1') fold.open = true;
        } catch (e) { /* no memory, no matter */ }
        fold.addEventListener('toggle', () => {
            try { localStorage.setItem(REMEMBER, fold.open ? '1' : '0'); } catch (e) { /* fine */ }
            if (fold.open) refreshBlender();
        });

        $('ringSizeSystem').addEventListener('change', () => {
            const iso = $('ringSizeSystem').value === 'iso';
            $('ringSizeLabel').textContent = iso ? 'Inner circumference (mm)' : 'US size';
            const box = $('ringSize');
            box.step = iso ? 0.5 : 0.25;
            box.min = iso ? 35 : 1;
            box.max = iso ? 90 : 20;
            box.value = iso ? 54 : 7;
        });

        $('btnFindBlender').addEventListener('click', async () => {
            if (!api()) return;
            const path = await api().pick_blender_dialog();
            if (!path) return;
            const res = await api().set_blender_path(path);
            if (res && res.success === false) {
                $('ringBlenderState').textContent = res.error;
                $('ringBlenderState').className = 'ring-blender-state missing';
                return;
            }
            refreshBlender();
        });

        $('btnRingMeasure').addEventListener('click', measure);
        $('btnRingFix').addEventListener('click', fix);

        const ready = setInterval(() => {
            if (!api()) return;
            clearInterval(ready);
            refreshBlender();
        }, 300);
    });

    window.meshwrightRing = {
        refresh: updateButtons,
        clear: () => { $('ringReport').innerHTML = ''; $('ringResult').innerHTML = ''; },
    };
})();

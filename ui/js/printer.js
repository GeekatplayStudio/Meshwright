/*
 * The printer panel: choose a machine, and see what it can and cannot make.
 *
 * It reports. It never changes the model — what to do about a detail that is too
 * fine is the owner's decision, and there is more than one right answer (print it
 * bigger, print it on the other machine, thicken it, or accept the loss). So the
 * panel offers the facts, points at the places on the model, and stops there.
 *
 * Anything the engine could not confirm is shown as not confirmed, in the same
 * breath as the number it belongs to. A printability check that quietly guesses is
 * worse than none: it sends someone to a six-hour print.
 */
(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    const api = () => (window.pywebview && window.pywebview.api) || null;
    const REMEMBER = 'meshwright.printer';

    let machines = [];
    let chosen = null;

    const text = s => String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const num = n => Number(n).toLocaleString();

    /* ---------- choosing a machine ---------- */
    function fillMakers() {
        const makers = [...new Set(machines.map(m => m.maker))].sort((a, b) => a.localeCompare(b));
        $('printerMaker').innerHTML = makers.map(m => `<option value="${text(m)}">${text(m)}</option>`).join('');
    }

    function fillModels(maker) {
        const mine = machines.filter(m => m.maker === maker);
        // A machine your own slicer knows about is almost certainly the one you print
        // on, so it is worth saying which those are rather than marking them cryptically.
        $('printerModel').innerHTML = mine.map(m =>
            `<option value="${text(m.id)}">${text(m.model)}${m.installed ? ' — on this PC' : ''}</option>`).join('');
    }

    function describe(machine) {
        if (!machine) return '–';
        if (machine.technology === 'resin') {
            const pitch = machine.pixel_um;
            return `${machine.resolution[0]} × ${machine.resolution[1]} screen · ${pitch} µm per pixel`
                + ` · finest detail about ${(pitch * 2 / 1000).toFixed(3)} mm`
                + ` · plate ${machine.build_mm.join(' × ')} mm`;
        }
        return `${machine.nozzle_mm} mm nozzle · finest detail about ${machine.nozzle_mm.toFixed(2)} mm`
            + ` · plate ${machine.build_mm.join(' × ')} mm`;
    }

    function select(id) {
        chosen = machines.find(m => m.id === id) || null;
        $('printerSpec').textContent = describe(chosen);
        if (!chosen) return;
        const resin = chosen.technology === 'resin';
        $('printerUnitLabel').textContent = resin ? 'Pixel (µm)' : 'Nozzle (mm)';
        $('printerUnit').value = resin ? chosen.pixel_um : chosen.nozzle_mm;
        $('printerUnit').step = resin ? 0.5 : 0.05;
        $('printerLayer').value = chosen.layer_mm;
        try { localStorage.setItem(REMEMBER, id); } catch (e) { /* a preference, not a requirement */ }
    }

    /* ---------- showing what came back ---------- */
    function clear() {
        const box = $('printResult');
        if (box) box.innerHTML = '';
    }

    function show(report) {
        const box = $('printResult');
        const printer = report.printer;
        const verdictClass = !report.printable ? 'bad' : (report.issues.length ? 'warn' : 'good');

        const lines = [];
        lines.push(`<div class="print-verdict ${verdictClass}">${text(report.verdict)}</div>`);
        lines.push(`<p class="hint">Measured against ${text(printer.name)} — ${text(printer.basis)},`
            + ` so the finest thing it can make is about ${printer.min_feature_mm.toFixed(3)} mm.</p>`);

        if (report.wall) {
            lines.push(`<dl class="stats"><dt>Thinnest wall</dt><dd>${report.wall.thinnest_mm.toFixed(3)} mm</dd>`
                + `<dt>Typical wall</dt><dd>${report.wall.median_mm.toFixed(2)} mm</dd></dl>`);
        }

        if (!report.issues.length) {
            lines.push('<p class="hint">Every detail in this model is larger than this printer\'s '
                + 'smallest feature. Nothing will be lost.</p>');
        }

        box.innerHTML = lines.join('');

        // The faults themselves, each one able to point at itself on the model.
        const list = document.createElement('ul');
        list.className = 'issues print-issues';
        for (const issue of report.issues) {
            const item = document.createElement('li');
            item.className = issue.severity;
            const where = issue.location
                ? `<div class="loc">${num(issue.location.total)} spot${issue.location.total === 1 ? '' : 's'}`
                  + ` · region ≈ ${issue.location.extent} mm · click to show them</div>`
                : '';
            item.innerHTML = `<div><div class="t">${text(issue.title)}</div>`
                + `<div class="d">${text(issue.detail)}</div>${where}</div>`;
            if (issue.location && window.viewer) {
                item.classList.add('locatable');
                item.addEventListener('click', () => {
                    const already = item.classList.contains('active');
                    list.querySelectorAll('li').forEach(x => x.classList.remove('active'));
                    if (already) { window.viewer.clearHighlight(); return; }
                    item.classList.add('active');
                    window.viewer.highlight(issue.location);
                });
            }
            list.appendChild(item);
        }
        if (report.issues.length) box.appendChild(list);

        if (report.suggested_scale && report.suggested_scale > 1) {
            const fits = report.suggested_scale_fits;
            const note = document.createElement('p');
            note.className = 'hint print-advice';
            const tall = report.suggested_height_mm ? ` — about ${report.suggested_height_mm} mm tall` : '';
            note.innerHTML = fits === false
                ? `Every detail would survive at <strong>${report.suggested_scale}×</strong> this size`
                  + `${tall}, but that is larger than this printer's plate.`
                : `Printed at <strong>${report.suggested_scale}×</strong> this size${tall}, every detail `
                  + `would survive. Nothing has been changed — that is a size to print at, not an edit.`;
            box.appendChild(note);
        }

        // Anything unconfirmed is said plainly, beside the numbers it applies to.
        if (report.doubts && report.doubts.length) {
            const caution = document.createElement('div');
            caution.className = 'print-doubt';
            caution.innerHTML = '<strong>Not confirmed.</strong> '
                + report.doubts.map(d => text(d.charAt(0).toUpperCase() + d.slice(1)) + '.').join(' ')
                + ' Repairing the model first makes this check exact.';
            box.appendChild(caution);
        }
    }

    /* ---------- running it ---------- */
    async function run() {
        if (!api() || !chosen) return;
        const button = $('btnPrintCheck');
        button.disabled = true;
        const previous = button.textContent;
        button.textContent = 'Checking…';
        try {
            const resin = chosen.technology === 'resin';
            const unit = parseFloat($('printerUnit').value) || 0;
            const result = await api().check_printability(
                chosen.id, chosen.technology,
                resin ? unit : 0, resin ? 0 : unit,
                parseFloat($('printerLayer').value) || 0,
                $('chkThorough').checked);
            if (!result || !result.success) {
                $('printResult').innerHTML =
                    `<div class="print-doubt">${text((result && result.error) || 'The check could not be made.')}</div>`;
                return;
            }
            show(result.report);
        } catch (e) {
            $('printResult').innerHTML = `<div class="print-doubt">${text(e.message)}</div>`;
        } finally {
            button.textContent = previous;
            button.disabled = false;
        }
    }

    /* ---------- wiring ---------- */
    async function load() {
        if (!api()) return;
        let listed;
        try {
            listed = await api().list_printers();
        } catch (e) {
            return;
        }
        if (!listed || !listed.success) return;
        machines = listed.machines || [];
        if (!machines.length) return;
        fillMakers();

        let wanted = null;
        try { wanted = localStorage.getItem(REMEMBER); } catch (e) { /* no memory, no matter */ }
        // Failing a remembered choice, start on a machine this PC's slicer knows
        // about: that is the one they are most likely to be printing on.
        const start = machines.find(m => m.id === wanted)
            || machines.find(m => m.installed)
            || machines[0];
        $('printerMaker').value = start.maker;
        fillModels(start.maker);
        $('printerModel').value = start.id;
        select(start.id);
    }

    document.addEventListener('DOMContentLoaded', () => {
        $('printerMaker').addEventListener('change', () => {
            fillModels($('printerMaker').value);
            select($('printerModel').value);
        });
        $('printerModel').addEventListener('change', () => select($('printerModel').value));
        $('btnPrintCheck').addEventListener('click', run);

        const ready = setInterval(() => {
            if (api()) { clearInterval(ready); load(); }
        }, 300);
    });

    window.meshwrightPrinter = { clear, reload: load };
})();

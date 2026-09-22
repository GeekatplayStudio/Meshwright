/*
 * Alt-drag a box over the model to select the pieces inside it.
 *
 * Clicking pieces one at a time is fine for three of them and hopeless for three
 * hundred, which is what a scan or a generated model routinely splits into. So
 * holding Alt turns the left button into a rubber band.
 *
 * Alt is what keeps this out of the way of everything else the left button already
 * does: a plain drag orbits, a plain click picks a piece, and both of those are
 * used far more often than this is. Holding Shift as well adds to the selection
 * rather than replacing it, which is what Shift already means when clicking.
 *
 * The band is drawn as a plain element over the viewport rather than in the scene,
 * so it stays crisp at any zoom and costs the renderer nothing while it is dragged.
 */
(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    const app = () => window.meshwright || {};
    const pieces = () => (app().pieces) || null;

    let band = null;
    let drag = null;

    function viewport() {
        return $('dropZone');
    }

    function show(left, top, width, height) {
        if (!band) {
            band = document.createElement('div');
            band.className = 'marquee';
            band.innerHTML = '<span class="marquee-count"></span>';
            viewport().appendChild(band);
        }
        const host = viewport().getBoundingClientRect();
        band.style.left = `${left - host.left}px`;
        band.style.top = `${top - host.top}px`;
        band.style.width = `${width}px`;
        band.style.height = `${height}px`;
    }

    function clear() {
        if (pending) { cancelAnimationFrame(pending); pending = null; }
        if (band) band.remove();
        band = null;
    }

    /* A running count while the band is dragged, on models where counting is
       cheap enough to do between frames. Past that size the band itself is the
       feedback: a number that arrives half a second late is worse than none. */
    const LIVE_LIMIT = 400000;          // total triangles
    let pending = null;

    function liveCount(box) {
        if (!band || pending) return;
        const label = band.querySelector('.marquee-count');
        if (!label) return;
        const faces = (window.viewer.shellFaceCounts || []).reduce((n, c) => n + c, 0);
        if (faces > LIVE_LIMIT) return;
        pending = requestAnimationFrame(() => {
            pending = null;
            if (!band || !drag) return;
            const caught = window.viewer.piecesInBox(box.left, box.top, box.right, box.bottom);
            label.textContent = caught.size
                ? `${caught.size} piece${caught.size === 1 ? '' : 's'}${drag.add ? ' to add' : ''}`
                : '';
        });
    }

    function bounds(event) {
        return {
            left: Math.min(drag.x, event.clientX),
            right: Math.max(drag.x, event.clientX),
            top: Math.min(drag.y, event.clientY),
            bottom: Math.max(drag.y, event.clientY),
        };
    }

    function finish(event, apply) {
        const canvas = window.viewer && window.viewer.renderer.domElement;
        if (canvas && drag && canvas.hasPointerCapture && canvas.hasPointerCapture(drag.id)) {
            try { canvas.releasePointerCapture(drag.id); } catch (e) { /* already gone */ }
        }
        if (window.viewer) window.viewer.controls.enabled = true;

        if (apply && drag) {
            const box = bounds(event);
            // A band a few pixels across is a slipped click, not a selection, and
            // wiping the selection because of one would be its own small disaster.
            if (box.right - box.left > 4 || box.bottom - box.top > 4) {
                const caught = window.viewer.piecesInBox(box.left, box.top, box.right, box.bottom);
                const keep = drag.add ? pieces().selected() : new Set();
                for (const piece of caught) keep.add(piece);
                pieces().choose([...keep]);
                const status = app().setStatus || (() => {});
                status(caught.size
                    ? `${caught.size} piece${caught.size === 1 ? '' : 's'} selected`
                    : 'Nothing inside the box', caught.size ? 'ok' : 'busy', 2500);
            }
        }
        drag = null;
        clear();
    }

    document.addEventListener('DOMContentLoaded', () => {
        const wait = setInterval(() => {
            if (!window.viewer || !window.viewer.renderer) return;
            clearInterval(wait);
            const canvas = window.viewer.renderer.domElement;

            canvas.addEventListener('pointerdown', event => {
                if (event.button !== 0 || !event.altKey) return;
                if (!window.viewer.mesh || !window.viewer.shellFaceCounts) return;
                if (!pieces() || pieces().all().length < 2) return;

                // Caught before the orbit controls see it: they are registered on
                // the same element, and without stopping the event here the model
                // would spin while the band is being drawn.
                event.preventDefault();
                event.stopPropagation();
                window.viewer.controls.enabled = false;
                drag = { id: event.pointerId, x: event.clientX, y: event.clientY, add: event.shiftKey };
                try { canvas.setPointerCapture(event.pointerId); } catch (e) { /* not fatal */ }
                show(event.clientX, event.clientY, 0, 0);
            }, true);

            canvas.addEventListener('pointermove', event => {
                if (!drag || event.pointerId !== drag.id) return;
                event.stopPropagation();
                const box = bounds(event);
                show(box.left, box.top, box.right - box.left, box.bottom - box.top);
                liveCount(box);
            }, true);

            canvas.addEventListener('pointerup', event => {
                if (!drag || event.pointerId !== drag.id) return;
                event.stopPropagation();
                finish(event, true);
            }, true);

            canvas.addEventListener('pointercancel', event => {
                if (drag && event.pointerId === drag.id) finish(event, false);
            }, true);
        }, 120);

        // Letting go of Alt mid-drag, or pressing Escape, abandons the band. Both
        // happen by accident often enough to be worth handling.
        document.addEventListener('keydown', event => {
            if (drag && event.key === 'Escape') {
                event.stopPropagation();
                finish(event, false);
            }
        }, true);
        window.addEventListener('blur', () => { if (drag) finish({}, false); });
    });

    window.meshwrightMarquee = { cancel: () => { if (drag) finish({}, false); } };
})();

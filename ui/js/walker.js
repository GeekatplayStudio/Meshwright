/* Meshwright — the little cup who walks along the bottom while you wait.
   Geekatplay Studio

   He appears whenever the engine starts something slow, strolls right to left
   across the foot of the window, and leaves when the work is done. Click him and
   he stops to offer you a coffee.

   The walk is a sprite strip stepped one frame at a time — no tweening — which is
   how it was drawn and how it should read. The hop on each step is in the drawings
   themselves; see the note above .walker-bob in style.css for why CSS deliberately
   does not add a second one.

   Two rules govern where he is, and both exist so he never teleports: he keeps
   looping until the work is done rather than stopping off-screen after one lap,
   and any change of mind picks him up from wherever he actually is. */
(() => {
    const CYCLE_MS = 660;          // eight drawings at 12fps, shot on twos
    const PX_PER_CYCLE = 96;       // ground covered by two steps, at his screen size
    const BALLOON_MS = 7000;       // how long the coffee offer stands on its own
    const COFFEE = 'https://geekatplay.gumroad.com/coffee';

    const api = () => (window.pywebview && window.pywebview.api) || null;

    let root = null, balloon = null;
    let busy = 0;                  // how many operations are running
    let leaving = null;            // timer that starts him walking off
    let dismiss = null;            // timer that takes the balloon back down
    // He is optional. Without his drawings he simply never turns up, rather than
    // walking an empty rectangle across the screen.
    let available = false;

    function build() {
        if (root) return root;
        root = document.createElement('div');
        root.className = 'walker';
        root.id = 'walker';
        root.setAttribute('role', 'button');
        root.setAttribute('tabindex', '0');
        root.setAttribute('aria-label', 'Buy Vlad a coffee');
        root.title = 'Buy Vlad a coffee';
        root.innerHTML = `
            <div class="walker-balloon" hidden>
                <p class="walker-balloon-text">Enjoying Meshwright?<br><b>Buy Vlad a coffee!</b></p>
                <span class="walker-balloon-link">geekatplay.gumroad.com/coffee</span>
            </div>
            <div class="walker-bob">
                <div class="walker-sprite">
                    <img class="walker-strip" src="assets/walk/walk-strip.png" alt="">
                </div>
            </div>`;

        const strip = root.querySelector('.walker-strip');
        strip.addEventListener('load', () => { available = true; });
        strip.addEventListener('error', () => {
            available = false;
            root.classList.remove('walking', 'resuming', 'leaving');
        });

        balloon = root.querySelector('.walker-balloon');
        root.addEventListener('click', toggleBalloon);
        root.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleBalloon(); }
        });
        document.body.appendChild(root);
        return root;
    }

    /* Stop mid-stride, say hello, and hold still until dismissed — by a click
       anywhere else, by Escape, or by nobody at all, because the offer takes
       itself back down and he carries on walking. */
    function toggleBalloon(event) {
        if (event) event.stopPropagation();
        build();
        if (!available) return;
        if (!balloon.hidden) { hideBalloon(); return; }

        root.classList.add('paused');
        balloon.hidden = false;
        // Restart the pop each time rather than letting it sit finished.
        balloon.classList.remove('pop');
        void balloon.offsetWidth;
        balloon.classList.add('pop');

        clearTimeout(dismiss);
        dismiss = setTimeout(hideBalloon, BALLOON_MS);
        document.addEventListener('click', onOutside, true);
        document.addEventListener('keydown', onEscape, true);
    }

    function hideBalloon() {
        if (!balloon || balloon.hidden) return;
        clearTimeout(dismiss);
        balloon.hidden = true;
        root.classList.remove('paused');          // and off he goes again
        document.removeEventListener('click', onOutside, true);
        document.removeEventListener('keydown', onEscape, true);
    }

    function onOutside(e) {
        if (root && root.contains(e.target)) return;
        // A click anywhere else dismisses him; if it landed on the balloon's own
        // link we open the page first.
        hideBalloon();
    }

    function onEscape(e) {
        if (e.key === 'Escape') { e.stopPropagation(); hideBalloon(); }
    }

    /* The balloon itself is the button: clicking it opens the page. */
    function openCoffee(e) {
        e.stopPropagation();
        if (api()) api().open_url(COFFEE); else window.open(COFFEE, '_blank');
        hideBalloon();
    }

    /* Where he is right now, in pixels from the left edge. */
    function currentX() {
        const t = getComputedStyle(root).transform;
        if (!t || t === 'none') return window.innerWidth;
        return new DOMMatrixReadOnly(t).m41;
    }

    /* One pace for every journey. Timing a walk by the clock is what makes a
       cartoon skate: his legs run at a fixed rate, so the only honest duration is
       the one that moves him the distance his feet claim to have covered. */
    function msFor(distance) {
        return Math.max(400, (Math.abs(distance) / PX_PER_CYCLE) * CYCLE_MS);
    }

    function fullLap() {
        return window.innerWidth + root.offsetWidth * 2.2;
    }

    /* Walk out to the left from wherever he is standing. Used both when the work
       finishes and when a lap is interrupted, so neither ever snaps him back. */
    function walkOutFrom(from) {
        root.style.setProperty('--walk-exit-from', `${from}px`);
        root.style.setProperty('--walk-exit', `${msFor(from + root.offsetWidth * 1.2)}ms`);
    }

    function start(label) {
        build();
        if (!available) return;
        clearTimeout(leaving);
        busy += 1;
        if (label) root.dataset.doing = label;
        if (root.classList.contains('walking') ||
            root.classList.contains('resuming')) return;    // already on his way

        root.style.setProperty('--walk-cycle', `${CYCLE_MS}ms`);
        root.style.setProperty('--walk-cross', `${msFor(fullLap())}ms`);

        if (root.classList.contains('leaving')) {
            // He had started for the door. Pick him up at his current position and
            // keep him at the same pace; when he reaches the left edge the ordinary
            // loop takes over. Restarting him from the right edge here is what used
            // to make him appear to jump across the window.
            walkOutFrom(currentX());
            root.classList.remove('leaving');
            void root.offsetWidth;
            root.classList.add('resuming');
            return;
        }

        root.classList.remove('walking');
        void root.offsetWidth;                              // begin off the right edge
        root.classList.add('walking');
    }

    function stop() {
        busy = Math.max(0, busy - 1);
        if (busy > 0 || !root) return;
        if (!root.classList.contains('walking') && !root.classList.contains('resuming')) return;

        clearTimeout(leaving);
        leaving = setTimeout(() => {
            if (busy > 0) return;
            hideBalloon();

            // Carry on from where he actually is, at the same pace, rather than
            // snapping back to the right edge or vanishing mid-step.
            walkOutFrom(currentX());
            root.classList.remove('walking', 'resuming');
            void root.offsetWidth;
            root.classList.add('leaving');
        }, 700);
    }

    function reset() {
        busy = 0;
        clearTimeout(leaving);
        if (!root) return;
        hideBalloon();
        root.classList.remove('walking', 'resuming', 'leaving');
    }

    document.addEventListener('DOMContentLoaded', () => {
        build();
        root.addEventListener('animationend', (e) => {
            if (e.animationName !== 'walker-exit') return;
            if (root.classList.contains('resuming')) {
                // He is off the left edge now, so handing him back to the endless
                // lap — which begins off the right edge — is a move nobody can see.
                root.classList.remove('resuming');
                root.classList.add('walking');
            } else {
                root.classList.remove('leaving');    // done; take him off the page
            }
        });
        root.querySelector('.walker-balloon-link').addEventListener('click', openCoffee);
        root.querySelector('.walker-balloon').addEventListener('click', openCoffee);
    });

    window.meshwrightWalker = {
        start, stop, reset,
        show: toggleBalloon,
        get available() { return available; },
    };
})();

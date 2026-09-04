/* Meshwright — the little cup who walks along the bottom while you wait.
   Geekatplay Studio

   He appears whenever the engine starts something slow, strolls right to left
   across the foot of the window, and leaves when the work is done. Click him and
   he stops to offer you a coffee.

   The walk is a sprite strip stepped one frame at a time — no tweening — which is
   how it was drawn and how it should read. Everything else is deliberately loose:
   he bobs, he leans, and the balloon wobbles into place, because a rubber-hose
   cartoon that moves on rails looks wrong. */
(() => {
    const CYCLE_MS = 800;          // one full two-step cycle, eight drawings
    const CROSS_MS = 26000;        // right edge to left edge at a strolling pace
    const COFFEE = 'https://geekatplay.gumroad.com/coffee';

    const api = () => (window.pywebview && window.pywebview.api) || null;

    let root = null, balloon = null;
    let busy = 0;                  // how many operations are running
    let leaving = null;            // timer that removes him after the last one
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
            root.classList.remove('walking', 'leaving');
        });

        balloon = root.querySelector('.walker-balloon');
        root.addEventListener('click', toggleBalloon);
        root.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleBalloon(); }
        });
        document.body.appendChild(root);
        return root;
    }

    /* Stop mid-stride, say hello, and hold still until dismissed. */
    function toggleBalloon(event) {
        if (event) event.stopPropagation();
        build();
        if (!available) return;
        const showing = !balloon.hidden;
        if (showing) { hideBalloon(); return; }

        root.classList.add('paused');
        balloon.hidden = false;
        // Restart the pop each time rather than letting it sit finished.
        balloon.classList.remove('pop');
        void balloon.offsetWidth;
        balloon.classList.add('pop');

        document.addEventListener('click', onOutside, true);
        document.addEventListener('keydown', onEscape, true);
    }

    function hideBalloon() {
        if (!balloon || balloon.hidden) return;
        balloon.hidden = true;
        root.classList.remove('paused');
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
        const m = new DOMMatrixReadOnly(t);
        return m.m41;
    }

    function start(label) {
        build();
        if (!available) return;
        clearTimeout(leaving);
        busy += 1;
        if (label) root.dataset.doing = label;
        if (root.classList.contains('walking')) return;   // already on his way

        root.classList.remove('leaving');
        root.style.removeProperty('--walk-exit-from');
        root.style.setProperty('--walk-cycle', `${CYCLE_MS}ms`);
        root.style.setProperty('--walk-cross', `${CROSS_MS}ms`);
        root.classList.remove('walking');
        void root.offsetWidth;                            // restart from the right edge
        root.classList.add('walking');
    }

    function stop() {
        busy = Math.max(0, busy - 1);
        if (busy > 0 || !root || !root.classList.contains('walking')) return;

        clearTimeout(leaving);
        leaving = setTimeout(() => {
            if (busy > 0) return;
            hideBalloon();

            // Carry on from where he actually is, at the same pace, rather than
            // snapping back to the right edge or vanishing mid-step.
            const from = currentX();
            const distance = from + root.offsetWidth * 1.2;
            const speed = (window.innerWidth + root.offsetWidth * 2.2) / CROSS_MS;

            root.style.setProperty('--walk-exit-from', `${from}px`);
            root.style.setProperty('--walk-exit', `${Math.max(400, distance / speed)}ms`);
            root.classList.remove('walking');
            void root.offsetWidth;
            root.classList.add('leaving');
        }, 700);
    }

    function reset() {
        busy = 0;
        clearTimeout(leaving);
        if (!root) return;
        hideBalloon();
        root.classList.remove('walking', 'leaving');
    }

    document.addEventListener('DOMContentLoaded', () => {
        build();
        // Once he is off the left edge, take him off the page entirely.
        root.addEventListener('animationend', (e) => {
            if (e.animationName === 'walker-exit') root.classList.remove('leaving');
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

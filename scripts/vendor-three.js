/**
 * Copies the pinned Three.js build and helpers into ui/vendor/.
 * Meshwright never loads scripts from a CDN — the desktop app must work offline.
 * Runs automatically after `npm install`.
 */
const fs = require('fs');
const path = require('path');

const files = [
    ['three/build/three.min.js', 'three.min.js'],
    ['three/examples/js/controls/OrbitControls.js', 'OrbitControls.js'],
    ['three/examples/js/controls/TransformControls.js', 'TransformControls.js'],
];

const root = path.join(__dirname, '..');
const dest = path.join(root, 'ui', 'vendor');
fs.mkdirSync(dest, { recursive: true });

let copied = 0;
for (const [from, to] of files) {
    const src = path.join(root, 'node_modules', from);
    if (!fs.existsSync(src)) {
        console.warn(`[meshwright] missing ${from} — run "npm install three@0.128.0"`);
        continue;
    }
    fs.copyFileSync(src, path.join(dest, to));
    copied++;
}
console.log(`[meshwright] vendored ${copied}/${files.length} Three.js files into ui/vendor/`);

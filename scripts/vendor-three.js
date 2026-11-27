/**
 * Builds ui/vendor/three.bundle.min.js: Three.js plus the two controls and
 * the picking tree (three-mesh-bvh) the viewport uses, as one ordinary <script> that defines window.THREE.
 *
 * Why a bundle rather than loading Three.js as ES modules: Three.js stopped
 * shipping a classic build in r160, and a module script is refused unless the
 * server labels .js as JavaScript. pywebview's server takes that label from
 * Python's mimetypes, which reads the Windows registry — and on machines where
 * an editor has re-registered .js as text/plain, the viewport would come up
 * blank with nothing on screen to say why. A classic script has no such
 * dependency, and the rest of ui/js keeps reading THREE as a global.
 *
 * Meshwright never loads scripts from a CDN — the desktop app must work offline —
 * so the result is committed, and people installing from the ZIP never run this.
 * It runs after `npm install`. If the bundle cannot be built (no esbuild, a
 * failed native download), the committed file is left exactly as it was.
 */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const dest = path.join(root, 'ui', 'vendor');
const out = path.join(dest, 'three.bundle.min.js');

function keepExisting(reason) {
    const have = fs.existsSync(out) ? 'keeping the existing ui/vendor/three.bundle.min.js' :
        'and there is no ui/vendor/three.bundle.min.js — the viewport will be blank';
    console.warn(`[meshwright] could not bundle Three.js (${reason}); ${have}`);
}

let esbuild, revision, bvhVersion;
try {
    esbuild = require('esbuild');
    // three's "exports" map hides package.json from require(), so read it as a file.
    const pkg = path.join(root, 'node_modules', 'three', 'package.json');
    revision = JSON.parse(fs.readFileSync(pkg, 'utf8')).version.split('.')[1];
    bvhVersion = JSON.parse(fs.readFileSync(
        path.join(root, 'node_modules', 'three-mesh-bvh', 'package.json'), 'utf8')).version;
} catch (e) {
    keepExisting(e.code === 'MODULE_NOT_FOUND' || e.code === 'ENOENT' ?'run "npm install" to fetch three, three-mesh-bvh and esbuild' : e.message);
    process.exit(0);
}

const entry = `
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
// three-mesh-bvh: a search tree over the triangles, so picking a piece on a
// multi-million-face model does not test every triangle. Only what viewer.js
// uses is named here, which lets the bundler leave the rest of it out.
import { MeshBVH, acceleratedRaycast, NOT_INTERSECTED, INTERSECTED, CONTAINED } from 'three-mesh-bvh';
// The namespace object is frozen, so the additions go on a copy of it.
window.THREE = Object.assign({}, THREE, {
    OrbitControls, TransformControls,
    MeshBVH, acceleratedRaycast, BVH_NOT_INTERSECTED: NOT_INTERSECTED, BVH_INTERSECTED: INTERSECTED, BVH_CONTAINED: CONTAINED,
});
`;

const tmp = out + '.tmp';
try {
    fs.mkdirSync(dest, { recursive: true });
    esbuild.buildSync({
        stdin: { contents: entry, resolveDir: root, loader: 'js' },
        bundle: true,
        format: 'iife',
        minify: true,
        target: 'es2020',
        legalComments: 'eof',
        banner: { js: `/* Three.js r${revision} with OrbitControls and TransformControls — MIT, https://threejs.org; ` +
                      `three-mesh-bvh ${bvhVersion} — MIT, https://github.com/gkjohnson/three-mesh-bvh. ` +
                      'Built by scripts/vendor-three.js; do not edit. */' },
        outfile: tmp,
        logLevel: 'silent',
    });
    // A bundle that does not define the three things viewer.js reaches for is
    // worse than the old one, so check before replacing it.
    const sandbox = { window: {} };
    sandbox.self = sandbox.window;
    require('vm').runInNewContext(fs.readFileSync(tmp, 'utf8'), sandbox);
    const T = sandbox.window.THREE;
    if (!T || !T.WebGLRenderer || !T.OrbitControls || !T.TransformControls || !T.MeshBVH || !T.acceleratedRaycast) {
        throw new Error('the bundle does not define THREE.WebGLRenderer, OrbitControls, TransformControls and MeshBVH');
    }
    fs.renameSync(tmp, out);
} catch (e) {
    try { fs.unlinkSync(tmp); } catch { /* nothing was written */ }
    keepExisting(e.message);
    process.exit(0);
}

// The pre-r160 files this script used to copy; a stale copy left beside the
// bundle would only mislead whoever reads ui/vendor next.
for (const old of ['three.min.js', 'OrbitControls.js', 'TransformControls.js']) {
    try { fs.unlinkSync(path.join(dest, old)); } catch { /* already gone */ }
}
console.log(`[meshwright] bundled Three.js r${revision} into ui/vendor/three.bundle.min.js`);

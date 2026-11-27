const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const test = require('node:test');
// The bundle the window really loads rather than node_modules/three, so what is
// tested here is what ships.
const shipped = { window: {} };
shipped.self = shipped.window;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../ui/vendor/three.bundle.min.js'), 'utf8'), shipped);
const THREE = shipped.window.THREE;
const context = vm.createContext({ THREE, console: { warn() {} }, window: {}, document: { addEventListener() {} } });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui/js/viewer.js'), 'utf8') +
    '\nglobalThis.Viewer = ModelViewer;', context);
const Viewer = context.Viewer;

test('click replaces selection, Shift-click toggles, background clears only without Shift', () => {
    let selected = Viewer.pickSelection(new Set([0, 1]), 2, false);
    assert.deepEqual([...selected], [2]);
    selected = Viewer.pickSelection(selected, 0, true);
    assert.deepEqual([...selected], [2, 0]);
    selected = Viewer.pickSelection(selected, 2, true);
    assert.deepEqual([...selected], [0]);
    assert.deepEqual([...Viewer.pickSelection(selected, null, true)], [0]);
    assert.deepEqual([...Viewer.pickSelection(selected, null, false)], []);
});

test('ray picking uses shell face ranges, nearest surface, and world transforms', () => {
    const viewer = Object.create(Viewer.prototype);
    viewer.renderer = { domElement: { getBoundingClientRect: () =>
        ({ left: 40, top: 20, width: 400, height: 400 }) } };
    viewer.camera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
    viewer.camera.position.z = 10;
    const geometry = new THREE.BufferGeometry();
    // Two triangles along the same ray; piece 1 is in front of piece 0.
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([
        -1, -1, 0, 1, -1, 0, 0, 1, 0,
        -1, -1, 2, 1, -1, 2, 0, 1, 2,
    ], 3));
    geometry.setIndex([0, 1, 2, 3, 4, 5]);
    viewer.mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
    viewer.shellFaceCounts = [1, 1];
    assert.equal(viewer.pieceAt(240, 220), 1);
    assert.equal(viewer.pieceAt(41, 21), null);
    const group = new THREE.Group();
    group.add(viewer.mesh);
    group.rotation.y = Math.PI;
    assert.equal(viewer.pieceAt(240, 220), 0);
    // Empty preview blocks must not shift the shell IDs.
    viewer.shellFaceCounts = [0, 1, 1];
    assert.equal(viewer.pieceAt(240, 220), 1);
    viewer.mesh = null;
    assert.equal(viewer.pieceAt(240, 220), null);
});

test('dragging, cancelled pointers, multiple touches and gizmo use do not select', () => {
    const viewer = Object.create(Viewer.prototype);
    const handlers = {};
    viewer.renderer = { domElement: { addEventListener: (type, fn) => { handlers[type] = fn; } } };
    viewer.mesh = {};
    viewer.shellFaceCounts = [1, 1];
    viewer.pieceAt = () => 1;
    const picks = [];
    viewer.onPiecePick = (...args) => picks.push(args);
    viewer.initPiecePicking();
    const event = { pointerId: 1, button: 0, clientX: 20, clientY: 20, shiftKey: true };
    const send = (type, extra = {}) => handlers[type]({ ...event, ...extra });
    send('pointerdown'); send('pointerup');
    assert.deepEqual(picks, [[1, true]]);
    picks.length = 0;
    send('pointerdown'); send('pointermove', { clientX: 40 });
    send('pointermove'); send('pointerup'); // returning to the starting point is still a drag
    send('pointerdown'); send('pointercancel'); send('pointerup');
    send('pointerdown'); send('lostpointercapture'); send('pointerup');
    send('pointerdown', { button: 2 }); send('pointerup', { button: 2 });
    send('pointerdown'); send('pointerdown', { pointerId: 2, isPrimary: false }); send('pointerup');
    viewer.gizmoOn = true; viewer.gizmo = { axis: 'X' };
    send('pointerdown'); send('pointerup');
    assert.equal(picks.length, 0);
});

/* ---- the search tree (three-mesh-bvh) must change the speed, never the answer ---- */

// A grid of small spheres, one piece each, in one indexed geometry — the shape the
// viewport really holds: pieces as consecutive runs of faces.
function piecesScene(perSide) {
    const positions = [], index = [], counts = [];
    for (let i = 0; i < perSide; i++) for (let j = 0; j < perSide; j++) {
        const ball = new THREE.SphereGeometry(0.3, 10, 8);
        ball.translate(i - perSide / 2 + 0.5, j - perSide / 2 + 0.5, (i * 7 + j * 3) % 5 - 2);
        const base = positions.length / 3;
        positions.push(...ball.getAttribute('position').array);
        for (const v of ball.getIndex().array) index.push(base + v);
        counts.push(ball.getIndex().count / 3);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(index);
    const viewer = Object.create(Viewer.prototype);
    viewer.renderer = { domElement: { getBoundingClientRect: () =>
        ({ left: 40, top: 20, width: 800, height: 600 }) } };
    viewer.camera = new THREE.PerspectiveCamera(50, 800 / 600, 0.1, 100);
    viewer.camera.position.set(3, 2, 14);
    viewer.camera.lookAt(0, 0, 0);
    viewer.mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: THREE.DoubleSide }));
    viewer.mesh.rotation.set(0.3, -0.4, 0.1);   // a turned model: the tree works in the mesh's own space
    viewer.mesh.position.set(0.5, -0.25, 0);
    viewer.shellFaceCounts = counts;
    return viewer;
}

// The same viewer with the tree switched off: the plain search that came before it.
function withoutTree(viewer) {
    const plain = Object.create(viewer);
    plain.pickTree = () => null;
    return plain;
}

test('the tree leaves the face order alone, because a piece is a run of faces', () => {
    const viewer = piecesScene(4);
    const before = Array.from(viewer.mesh.geometry.getIndex().array);
    assert.ok(viewer.pickTree(), 'the bundle should provide MeshBVH');
    assert.deepEqual(Array.from(viewer.mesh.geometry.getIndex().array), before);
});

test('clicking picks the same piece with the tree as without it', () => {
    const viewer = piecesScene(6);
    const plain = withoutTree(viewer);
    let hits = 0;
    for (let x = 45; x < 840; x += 13) for (let y = 25; y < 620; y += 11) {
        const expected = plain.pieceAt(x, y);
        // The plain search above must run before the tree's raycast is installed on
        // the mesh; put the stock one back so each comparison is a fair one.
        viewer.mesh.raycast = THREE.Mesh.prototype.raycast;
        assert.equal(viewer.pieceAt(x, y), expected, `click at ${x},${y}`);
        viewer.mesh.raycast = THREE.Mesh.prototype.raycast;
        if (expected !== null) hits++;
    }
    assert.ok(hits > 50, `only ${hits} clicks landed on a piece — the scene is not testing much`);
});

test('a rubber band catches the same pieces with the tree as without it', () => {
    const viewer = piecesScene(6);
    const plain = withoutTree(viewer);
    let seed = 12345;
    const random = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
    let caught = 0, empty = 0;
    const boxes = [[40, 20, 840, 620], [0, 0, 2000, 2000], [400, 300, 400, 300], [40, 20, 45, 25]];
    for (let n = 0; n < 300; n++) {
        const x = 40 + random() * 800, y = 20 + random() * 600;
        boxes.push([x, y, x + random() * 300, y + random() * 250]);
    }
    for (const [l, t, r, b] of boxes) {
        const expected = [...plain.piecesInBox(l, t, r, b)].sort((a, c) => a - c);
        const got = [...viewer.piecesInBox(l, t, r, b)].sort((a, c) => a - c);
        assert.deepEqual(got, expected, `box ${[l, t, r, b].map(Math.round)}`);
        caught += expected.length;
        if (!expected.length) empty++;
    }
    assert.ok(caught > 500 && empty > 0, `caught ${caught}, empty ${empty}: both cases must be exercised`);
});

test('moving or turning the model throws the tree away, so picks follow the model', () => {
    const viewer = piecesScene(3);
    viewer.requestRender = () => {};
    viewer.pickTree();
    viewer.moveHandle = new THREE.Object3D();
    viewer.moveHandle.position.set(0, 0, 5);
    viewer.moveLast = new THREE.Vector3();
    viewer.moveVerts = viewer.verticesOfPieces(new Set([0]));
    viewer.dragPieces();
    assert.equal(viewer.mesh.geometry.boundsTree, null, 'a drag moved vertices under a stale tree');

    viewer.pickTree();
    viewer.modelGroup = new THREE.Group();
    viewer.clearHighlight = () => {};
    viewer.bakeGroupRotation([[-5, -5, -5], [5, 5, 5]]);
    assert.equal(viewer.mesh.geometry.boundsTree, null, 'a baked rotation left a stale tree');
});

test('with no tree to be had, picking still works the plain way', () => {
    const viewer = piecesScene(3);
    const saved = THREE.MeshBVH;
    THREE.MeshBVH = function () { throw new Error('out of memory'); };
    try {
        const all = viewer.piecesInBox(0, 0, 2000, 2000);
        assert.equal(all.size, 9);
        assert.equal(viewer.mesh.geometry.userData.noPickTree, true);
    } finally {
        THREE.MeshBVH = saved;
    }
});

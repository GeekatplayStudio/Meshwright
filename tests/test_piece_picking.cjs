const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const test = require('node:test');
const THREE = require('three');
const context = vm.createContext({ THREE, window: {}, document: { addEventListener() {} } });
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

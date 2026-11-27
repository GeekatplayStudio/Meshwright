/* Meshwright viewport — Geekatplay Studio */

/* Every surface, line and light colour in this file was chosen by eye under
   Three.js r128, which used a hex value as-is and only converted on output. Since
   r152, Three.js reads a hex value as sRGB and converts it to linear first, which
   draws each of them noticeably darker. Marking them linear keeps the viewport
   looking the way it was tuned. The background is the exception — it is matched
   to the page's own #111315 and stays plain sRGB. */
function linear(hex) {
    return new THREE.Color().setHex(hex, THREE.LinearSRGBColorSpace);
}

/* r155 made lights physically based: a light of intensity 1 now gives 1/π of
   what it did, so every intensity this file sets (including the Power slider's)
   is scaled back up to look as it was tuned. */
const LIGHT_SCALE = Math.PI;

class ModelViewer {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.mesh = null;
        this.wire = null;
        this.edgeLines = null;
        this.mode = 'shaded';
        this.showWire = false;
        this.showEdges = true;
        this.lightAz = 45;
        this.lightEl = 55;
        this.lightDist = 300;
        this.init();
    }

    init() {
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x111315);

        const w = this.container.clientWidth || 800;
        const h = this.container.clientHeight || 600;
        this.camera = new THREE.PerspectiveCamera(40, w / h, 0.1, 20000);
        this.camera.position.set(150, 120, 200);

        try {
            this.renderer = new THREE.WebGLRenderer({ antialias: true });
        } catch (e) {
            this.showNoWebGL();
            throw e;
        }
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.setSize(w, h);
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 1.0;
        this.container.appendChild(this.renderer.domElement);

        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.08;
        this.needsRender = true;
        this.controls.addEventListener('change', () => { this.needsRender = true; });
        this.initPiecePicking();

        this.scene.add(new THREE.HemisphereLight(linear(0xdfe6f0), linear(0x2a2420), 0.55 * LIGHT_SCALE));
        this.key = new THREE.DirectionalLight(linear(0xfff4e0), 1.2 * LIGHT_SCALE);
        this.scene.add(this.key);
        this.fill = new THREE.DirectionalLight(linear(0x9fb8d8), 0.35 * LIGHT_SCALE);
        this.fill.position.set(-200, 80, -150);
        this.scene.add(this.fill);
        this.updateLight();

        this.grid = ModelViewer.makeGrid(256, 16);
        this.scene.add(this.grid);

        this.materials = {
            shaded: new THREE.MeshStandardMaterial({ color: linear(0xb9bec6), roughness: 0.55, metalness: 0.05, side: THREE.DoubleSide }),
            clay: new THREE.MeshStandardMaterial({ color: linear(0xd8b98a), roughness: 0.9, metalness: 0.0, side: THREE.DoubleSide }),
            normals: new THREE.MeshNormalMaterial({ side: THREE.DoubleSide }),
            xray: new THREE.MeshBasicMaterial({ color: linear(0x9fc4ff), transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false }),
            shells: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.92, metalness: 0.0, side: THREE.DoubleSide }),
            pbr: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5, metalness: 0.05, side: THREE.DoubleSide }),
            albedo: new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }),
            normal_map: new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }),
            roughness_map: new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }),
            metallic_map: new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }),
            ao_map: new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }),
            height_map: new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }),
        };
        this.textureData = null;
        this.displaceMm = 0;

        new ResizeObserver(() => this.resize()).observe(this.container);
        this.animate();
    }

    /* Three.js has needed WebGL 2 since r163. Without it the viewport would just be
       an empty dark box, so say what is wrong and what fixes it. */
    showNoWebGL() {
        const note = document.createElement('div');
        note.style.cssText = 'display:flex;align-items:center;justify-content:center;height:100%;box-sizing:border-box;' +
            'padding:24px;text-align:center;color:#e6e8eb;font:14px/1.5 sans-serif;';
        note.textContent = 'The 3D view could not start: this computer\'s graphics driver does not offer WebGL 2. ' +
            'Updating the graphics driver (from the Intel, AMD or NVIDIA website, or through Windows Update) ' +
            'usually fixes it. Then close Meshwright and open it again.';
        this.container.appendChild(note);
    }

    /* The grid's line colours are tuned like everything else here; see linear(). */
    static makeGrid(size, divisions) {
        return new THREE.GridHelper(size, divisions, linear(0x3a3f47), linear(0x22262b));
    }

    animate() {
        requestAnimationFrame(() => this.animate());
        const moved = this.controls ? this.controls.update() : false;
        if (moved || this.needsRender) {
            this.renderer.render(this.scene, this.camera);
            if (this.compassCtx) this.drawCompass(this.compassCtx, 72);
            this.needsRender = false;
        }
    }

    /* Ask for one more frame.
       The viewport renders on demand rather than continuously, so anything that
       changes what is on screen has to say so. Every method below that touches the
       scene, a material, the camera or a light calls this before it returns —
       including the ones whose change would happen to be picked up by orbit
       damping, because relying on that makes a control work only when the mouse
       happens to be moving. tests/test_viewport_invalidation.py holds the line. */
    requestRender() {
        this.needsRender = true;
    }

    resize() {
        const w = this.container.clientWidth, h = this.container.clientHeight;
        if (!w || !h) return;
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(w, h);
        this.requestRender();
    }

    /* Load indexed geometry: Float32 vertices, Uint32 faces, Uint32 boundary edge pairs. */
    loadGeometry(preview, shellFaceCounts) {
        this.requestRender();
        this.clear();
        if (!preview.uvs) {
            this.clearTextures();
        }
        const verts = new Float32Array(preview.vertices);
        const faces = new Uint32Array(preview.faces);
        const bEdges = new Uint32Array(preview.boundary_edges);

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(verts, 3));
        geometry.setIndex(new THREE.BufferAttribute(faces, 1));
        // Z-up model -> Y-up viewer
        geometry.rotateX(-Math.PI / 2);
        geometry.computeBoundingBox();
        const bb = geometry.boundingBox;
        const center = new THREE.Vector3();
        bb.getCenter(center);
        // Capture the offset first: translate() recomputes boundingBox in place.
        this.modelOffset = new THREE.Vector3(-center.x, -bb.min.y, -center.z);
        geometry.translate(this.modelOffset.x, this.modelOffset.y, this.modelOffset.z);
        geometry.computeVertexNormals();
        geometry.computeBoundingSphere();

        if (preview.uvs) {
            const uvs = (preview.uvs instanceof ArrayBuffer || ArrayBuffer.isView(preview.uvs))
                ? new Float32Array(preview.uvs.buffer || preview.uvs)
                : new Float32Array(Uint8Array.from(atob(preview.uvs), c => c.charCodeAt(0)).buffer);
            // Since r151 the AO map reads the same 'uv' channel as every other map
            // (texture.channel 0), so the 'uv2' copy r128 needed is gone.
            geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
        }

        this.shellFaceCounts = shellFaceCounts || null;
        this.modelGroup = new THREE.Group();
        this.scene.add(this.modelGroup);
        this.mesh = new THREE.Mesh(geometry, this.materials[this.mode]);
        this.modelGroup.add(this.mesh);

        // The wireframe overlay is built on demand: a WireframeGeometry for a
        // multi-million-face mesh costs seconds to build and starves the CPU while
        // it renders, which slows down whatever the backend is computing.
        this.wire = null;
        if (this.showWire) this.buildWire();

        if (bEdges.length) {
            const pos = geometry.getAttribute('position');
            const arr = new Float32Array(bEdges.length * 3);
            for (let i = 0; i < bEdges.length; i++) {
                const v = bEdges[i];
                arr[i * 3] = pos.getX(v); arr[i * 3 + 1] = pos.getY(v); arr[i * 3 + 2] = pos.getZ(v);
            }
            const eg = new THREE.BufferGeometry();
            eg.setAttribute('position', new THREE.BufferAttribute(arr, 3));
            this.edgeLines = new THREE.LineSegments(eg, new THREE.LineBasicMaterial({ color: linear(0xe5534b), depthTest: false }));
            this.edgeLines.renderOrder = 10;
            this.edgeLines.visible = this.showEdges;
            this.modelGroup.add(this.edgeLines);
        }
        if (this.gizmoOn) this.attachGizmo(true);
        this.fit();
        return bEdges.length / 2;
    }

    /* Orbit and pan remain drags; only a primary-button click selects a piece. */
    initPiecePicking() {
        const canvas = this.renderer.domElement;
        let press = null;
        canvas.addEventListener('pointerdown', e => {
            if (e.button !== 0 || e.isPrimary === false) { press = null; return; }
            // Alt belongs to the area-select drag; a click that begins with it held
            // is the start of a rubber band, not a pick.
            if (e.altKey) { press = null; return; }
            if (this.gizmoOn && this.gizmo && (this.gizmo.axis || this.gizmo.dragging)) return;
            press = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
        }, true);
        canvas.addEventListener('pointermove', e => {
            if (press && e.pointerId === press.id &&
                Math.hypot(e.clientX - press.x, e.clientY - press.y) > 5) press.moved = true;
        }, true);
        canvas.addEventListener('pointercancel', () => { press = null; }, true);
        canvas.addEventListener('lostpointercapture', () => { press = null; }, true);
        canvas.addEventListener('pointerup', e => {
            const start = press;
            press = null;
            if (!start || start.id !== e.pointerId || start.moved || e.button !== 0 ||
                Math.hypot(e.clientX - start.x, e.clientY - start.y) > 5) return;
            if (this.gizmoOn && this.gizmo && (this.gizmo.axis || this.gizmo.dragging)) return;
            if (!this.onPiecePick || !this.mesh || !this.shellFaceCounts) return;
            this.onPiecePick(this.pieceAt(e.clientX, e.clientY), e.shiftKey);
        }, true);
    }

    pieceAt(clientX, clientY) {
        if (!this.mesh || !this.shellFaceCounts) return null;
        const rect = this.renderer.domElement.getBoundingClientRect();
        if (!rect.width || !rect.height) return null;
        const pointer = new THREE.Vector2(
            (clientX - rect.left) / rect.width * 2 - 1,
            -(clientY - rect.top) / rect.height * 2 + 1);
        const ray = new THREE.Raycaster();
        this.camera.updateMatrixWorld();
        this.mesh.updateWorldMatrix(true, false);
        ray.setFromCamera(pointer, this.camera);
        if (this.pickTree()) {
            // Walk the tree instead of testing every triangle, and stop at the
            // nearest hit — the only one a click is asking about.
            this.mesh.raycast = THREE.acceleratedRaycast;
            ray.firstHitOnly = true;
        }
        const hit = ray.intersectObject(this.mesh, false)[0];
        if (!hit) return null;
        // Preview faces are grouped by shell, including at reduced detail.
        let end = 0;
        for (let i = 0; i < this.shellFaceCounts.length; i++) {
            end += this.shellFaceCounts[i];
            if (hit.faceIndex < end) return i;
        }
        return null;
    }

    /* The search tree over the model's triangles (three-mesh-bvh), which is what
       lets a click or a rubber band on a multi-million-face model answer without
       visiting every triangle.

       Built the first time it is needed rather than on load, for the same reason
       as the wireframe: most models are opened, checked and exported without a
       single pick, and building it costs about a second per few million faces.

       `indirect` matters. By default the tree reorders the index buffer to suit
       itself, and a piece here IS a run of faces in that buffer — reordered, every
       pick would report the wrong piece. Indirect leaves the buffer alone.

       Returns null if the tree cannot be had (an old bundle, or no memory for it);
       both callers then fall back to the plain search they used before. */
    pickTree() {
        if (!this.mesh || !THREE.MeshBVH) return null;
        const geometry = this.mesh.geometry;
        if (!geometry.boundsTree && !geometry.userData.noPickTree) {
            try {
                geometry.boundsTree = new THREE.MeshBVH(geometry, { indirect: true });
            } catch (e) {
                geometry.boundsTree = null;
                geometry.userData.noPickTree = true;   // do not retry on every click
                console.warn('Picking falls back to the plain search: the search tree could not be built.', e);
            }
        }
        return geometry.boundsTree || null;
    }

    /* The tree describes the vertices as they were when it was built. Anything
       that moves them has to drop it, or picks land where the model used to be. */
    dropPickTree() {
        if (this.mesh) this.mesh.geometry.boundsTree = null;
    }

    static pickSelection(selected, piece, toggle) {
        const next = new Set(toggle ? selected : []);
        if (piece !== null) {
            if (toggle && next.has(piece)) next.delete(piece);
            else next.add(piece);
        }
        return next;
    }

    /* Selection is red and issue highlights are cyan, so the shell palette
       avoids both: blue, violet, green, amber, magenta, lime, indigo, orange. */
    static shellColor(i, selected) {
        // These go straight into a vertex-colour buffer, which Three.js reads as
        // linear; they are tuned as linear values, the same as linear() above.
        const c = new THREE.Color();
        if (selected) return c.setHex(0xe5534b, THREE.LinearSRGBColorSpace);
        const base = [0.62, 0.78, 0.36, 0.12, 0.88, 0.24, 0.70, 0.07];
        const hue = base[i % base.length];
        // Deep and saturated: a large piece under the key light washes out otherwise.
        const light = 0.44 - 0.08 * (Math.floor(i / base.length) % 3);
        c.setHSL(hue, 0.72, light, THREE.LinearSRGBColorSpace);
        return c;
    }

    /* Colour each shell; `selected` is a Set of shell indices to mark for removal. */
    showShells(selected) {
        if (!this.mesh || !this.shellFaceCounts) return;
        const geometry = this.mesh.geometry;
        const index = geometry.getIndex().array;
        const nV = geometry.getAttribute('position').count;
        const colors = new Float32Array(nV * 3);
        let face = 0;
        this.shellFaceCounts.forEach((count, si) => {
            const c = ModelViewer.shellColor(si, selected && selected.has(si));
            for (let k = 0; k < count; k++, face++) {
                for (let j = 0; j < 3; j++) {
                    const v = index[face * 3 + j];
                    colors[v * 3] = c.r; colors[v * 3 + 1] = c.g; colors[v * 3 + 2] = c.b;
                }
            }
        });
        geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        this.mesh.material = this.materials.shells;
        this.shellMode = true;
        this.requestRender();
    }

    hideShells() {
        this.shellMode = false;
        if (this.mesh) this.mesh.material = this.materials[this.mode];
        this.requestRender();
    }

    /* Map a backend (Z-up) point into viewer space. */
    toViewer(p) {
        const v = new THREE.Vector3(p[0], p[1], p[2]);
        v.applyAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
        if (this.modelOffset) v.add(this.modelOffset);
        return v;
    }

    /* Highlight an issue location: points + a translucent "area" sphere, then frame it. */
    highlight(location) {
        this.clearHighlight();
        if (!location || !location.points || !location.points.length || !this.mesh) return;
        this.requestRender();
        const pts = location.points.map(p => this.toViewer(p));
        const radius = this.mesh.geometry.boundingSphere.radius;
        const group = new THREE.Group();

        const arr = new Float32Array(pts.length * 3);
        pts.forEach((v, i) => { arr[i * 3] = v.x; arr[i * 3 + 1] = v.y; arr[i * 3 + 2] = v.z; });
        const pg = new THREE.BufferGeometry();
        pg.setAttribute('position', new THREE.BufferAttribute(arr, 3));
        const dots = new THREE.Points(pg, new THREE.PointsMaterial({ color: linear(0x22d3ee), size: 8, sizeAttenuation: false, depthTest: false }));
        dots.renderOrder = 20;
        group.add(dots);

        // General-area marker so tiny defects are still findable
        const box = new THREE.Box3().setFromPoints(pts);
        const size = new THREE.Vector3();
        box.getSize(size);
        const areaR = Math.max(size.length() * 0.6, radius * 0.04);
        const center = new THREE.Vector3();
        box.getCenter(center);
        const sphere = new THREE.Mesh(new THREE.SphereGeometry(areaR, 24, 16),
            new THREE.MeshBasicMaterial({ color: linear(0x22d3ee), transparent: true, opacity: 0.10, depthWrite: false }));
        sphere.position.copy(center);
        group.add(sphere);
        const ring = new THREE.Mesh(new THREE.SphereGeometry(areaR, 24, 16),
            new THREE.MeshBasicMaterial({ color: linear(0x22d3ee), wireframe: true, transparent: true, opacity: 0.35, depthTest: false }));
        ring.position.copy(center);
        ring.renderOrder = 19;
        group.add(ring);

        this.highlightGroup = group;
        this.modelGroup.add(group);

        // Frame the region, never closer than a quarter of the model
        const frameR = Math.max(areaR * 2.5, radius * 0.25);
        const dist = frameR / Math.sin((this.camera.fov * Math.PI / 180) / 2);
        const dir = new THREE.Vector3().subVectors(this.camera.position, this.controls.target).normalize();
        const worldCenter = this.modelGroup.localToWorld(center.clone());
        this.controls.target.copy(worldCenter);
        this.camera.position.copy(worldCenter).addScaledVector(dir, dist);
        this.controls.update();
    }

    clearHighlight() {
        if (this.highlightGroup) {
            if (this.highlightGroup.parent) this.highlightGroup.parent.remove(this.highlightGroup);
            this.highlightGroup.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
            this.highlightGroup = null;
            this.requestRender();
        }
    }

    /* Preset camera views. */
    setView(name) {
        if (!this.mesh) return;
        const s = this.worldSphere();
        const dist = s.radius / Math.sin((this.camera.fov * Math.PI / 180) / 2) * 1.15;
        const dirs = {
            top: [0, 1, 0.0001], bottom: [0, -1, 0.0001], front: [0, 0, 1], back: [0, 0, -1],
            left: [-1, 0, 0], right: [1, 0, 0], iso: [0.6, 0.5, 0.75],
        };
        const d = new THREE.Vector3(...(dirs[name] || dirs.iso)).normalize();
        this.camera.position.copy(s.center).addScaledVector(d, dist);
        this.controls.target.copy(s.center);
        this.controls.update();
        this.requestRender();
    }

    /* Interactive rotation gizmo. Rotation happens instantly in the viewport;
       the backend is synced afterwards via onRotate (no geometry round-trip). */
    attachGizmo(on) {
        this.gizmoOn = on;
        if (!this.gizmo) {
            this.gizmo = new THREE.TransformControls(this.camera, this.renderer.domElement);
            this.gizmo.setMode('rotate');
            this.gizmo.setSpace('world');
            this.gizmo.addEventListener('change', () => { this.requestRender(); });
            this.gizmo.addEventListener('dragging-changed', e => { this.controls.enabled = !e.value; });
            this.gizmo.addEventListener('mouseUp', () => {
                if (this.onRotate) this.onRotate(this.modelMatrix());
            });
            // Since r169 the controls are not themselves in the scene; what draws is their helper.
            this.scene.add(this.gizmo.getHelper());
        }
        if (on && this.modelGroup) {
            this.gizmo.attach(this.modelGroup);
            this.gizmo.size = 0.8;
        } else {
            this.gizmo.detach();
        }
        this.requestRender();
        return this.gizmoOn;
    }

    /* Which pieces fall inside a rectangle drawn on screen.

       A piece counts as caught the moment any one of its points lands in the box —
       the "touch" rule a modelling program uses — because a rubber band that
       demanded a whole piece be enclosed would refuse everything on a zoomed-in
       model, which is exactly when one is most wanted.

       The search stops at the first point found inside each piece, so the usual case
       costs almost nothing; only a drag that catches nothing pays for projecting
       every vertex. */
    piecesInBox(left, top, right, bottom) {
        const found = new Set();
        if (!this.mesh || !this.shellFaceCounts) return found;
        const rect = this.renderer.domElement.getBoundingClientRect();
        if (!rect.width || !rect.height) return found;

        this.camera.updateMatrixWorld();
        this.mesh.updateWorldMatrix(true, false);
        const world = this.mesh.matrixWorld;
        const tree = this.pickTree();
        if (tree) return this.piecesInBoxByTree(tree, rect, left, top, right, bottom);

        const position = this.mesh.geometry.getAttribute('position');
        const index = this.mesh.geometry.getIndex().array;
        const point = new THREE.Vector3();

        let face = 0;
        this.shellFaceCounts.forEach((count, piece) => {
            const end = face + count;
            for (let f = face; f < end && !found.has(piece); f++) {
                for (let corner = 0; corner < 3; corner++) {
                    const v = index[f * 3 + corner];
                    point.set(position.getX(v), position.getY(v), position.getZ(v))
                        .applyMatrix4(world).project(this.camera);
                    // Behind the camera, projection folds a point back into view.
                    if (point.z < -1 || point.z > 1) continue;
                    const x = rect.left + (point.x * 0.5 + 0.5) * rect.width;
                    const y = rect.top + (-point.y * 0.5 + 0.5) * rect.height;
                    if (x >= left && x <= right && y >= top && y <= bottom) {
                        found.add(piece);
                        break;
                    }
                }
            }
            face = end;
        });
        return found;
    }


    /* The same answer as the loop above, found through the search tree.

       The rectangle on screen is a thin pyramid in space — the camera's own view
       volume, cropped to the box. Expressed in the mesh's own coordinates, whole
       branches of the tree that lie outside it are discarded without looking at
       a single triangle, which is what makes a drag that catches nothing — the
       expensive case for the loop — the cheapest one here. */
    piecesInBoxByTree(tree, rect, left, top, right, bottom) {
        const found = new Set();
        // Screen rectangle -> normalised device coordinates (y up).
        const x0 = (left - rect.left) / rect.width * 2 - 1;
        const x1 = (right - rect.left) / rect.width * 2 - 1;
        const y0 = -(bottom - rect.top) / rect.height * 2 + 1;
        const y1 = -(top - rect.top) / rect.height * 2 + 1;
        const w = Math.max(x1 - x0, 1e-9), h = Math.max(y1 - y0, 1e-9);
        // Stretch that rectangle to fill the view, so the ordinary six planes of a
        // camera's view volume become the six sides of the box that was drawn.
        const crop = new THREE.Matrix4().set(
            2 / w, 0, 0, -(x1 + x0) / w,
            0, 2 / h, 0, -(y1 + y0) / h,
            0, 0, 1, 0,
            0, 0, 0, 1);
        const toBox = crop.multiply(this.camera.projectionMatrix)
            .multiply(this.camera.matrixWorldInverse).multiply(this.mesh.matrixWorld);
        const volume = new THREE.Frustum().setFromProjectionMatrix(toBox);

        const position = this.mesh.geometry.getAttribute('position').array;
        const index = this.mesh.geometry.getIndex().array;
        const corner = new THREE.Vector3();
        const cornerInside = v => volume.containsPoint(
            corner.set(position[v * 3], position[v * 3 + 1], position[v * 3 + 2]));
        const faceInside = f => cornerInside(index[f * 3]) || cornerInside(index[f * 3 + 1]) ||
            cornerInside(index[f * 3 + 2]);

        // Where each piece's run of faces starts and ends.
        const counts = this.shellFaceCounts;
        const starts = new Array(counts.length), ends = new Array(counts.length);
        let total = 0, pieces = 0;
        for (let i = 0; i < counts.length; i++) {
            starts[i] = total; total += counts[i]; ends[i] = total;
            if (counts[i] > 0) pieces++;
        }

        // First, a glance at the first few faces of every piece. A band drawn round
        // the whole model catches each piece on its first vertex, and the tree — which
        // is organised by place, not by piece — would otherwise have to walk every
        // triangle in view to learn the same thing.
        for (let i = 0; i < counts.length; i++) {
            const stop = Math.min(ends[i], starts[i] + 4);
            for (let f = starts[i]; f < stop; f++) {
                if (faceInside(f)) { found.add(i); break; }
            }
        }
        if (found.size === pieces) return found;

        const pieceOf = face => {
            let lo = 0, hi = ends.length - 1;
            while (lo < hi) {
                const mid = (lo + hi) >> 1;
                if (face < ends[mid]) hi = mid; else lo = mid + 1;
            }
            return lo;
        };
        const whollyInside = box => {
            // Six planes, and the box corner furthest behind each one.
            for (const plane of volume.planes) {
                const n = plane.normal;
                if (n.x * (n.x > 0 ? box.min.x : box.max.x) + n.y * (n.y > 0 ? box.min.y : box.max.y) +
                    n.z * (n.z > 0 ? box.min.z : box.max.z) + plane.constant < 0) return false;
            }
            return true;
        };
        // Neighbouring triangles nearly always belong to one piece, so remember the
        // run the last one fell in and skip the search while that holds.
        let runStart = 0, runEnd = 0, runPiece = -1, runDone = false;

        tree.shapecast({
            intersectsBounds: box => {
                if (!volume.intersectsBox(box)) return THREE.BVH_NOT_INTERSECTED;
                return whollyInside(box) ? THREE.BVH_CONTAINED : THREE.BVH_INTERSECTED;
            },
            // Whole ranges at a time rather than a callback per triangle: for a branch
            // that lies entirely inside the band there is nothing to measure, only
            // pieces to tick off.
            intersectsRange: (offset, count, contained) => {
                for (let i = offset, end = offset + count; i < end; i++) {
                    const face = tree.resolveTriangleIndex(i);
                    if (face < runStart || face >= runEnd) {
                        runPiece = pieceOf(face);
                        runStart = starts[runPiece]; runEnd = ends[runPiece];
                        runDone = found.has(runPiece);
                    }
                    if (runDone) continue;
                    if (contained || faceInside(face)) {
                        found.add(runPiece);
                        runDone = true;
                        if (found.size === pieces) return true;   // everything is caught: stop looking
                    }
                }
                return false;
            },
        });
        return found;
    }


    /* ---------- moving chosen pieces ----------
       The viewport holds one mesh, not one object per piece, so a piece is a run of
       faces inside it. Moving one therefore means shifting the vertices those faces
       use — which is safe precisely because a piece is a connected component and
       shares no vertex with any other.

       The vertex list is worked out once when the drag begins, not per frame: on a
       half-million-face model, walking the index buffer sixty times a second is the
       difference between dragging and watching a slideshow. */
    verticesOfPieces(selected) {
        if (!this.mesh || !this.shellFaceCounts) return [];
        const index = this.mesh.geometry.getIndex().array;
        const wanted = new Set();
        let face = 0;
        this.shellFaceCounts.forEach((count, piece) => {
            if (selected.has(piece)) {
                for (let f = face; f < face + count; f++) {
                    wanted.add(index[f * 3]);
                    wanted.add(index[f * 3 + 1]);
                    wanted.add(index[f * 3 + 2]);
                }
            }
            face += count;
        });
        return Array.from(wanted);
    }

    /* Where the chosen pieces sit, in viewer space — where the handle belongs. */
    centreOfPieces(selected) {
        const vertices = this.verticesOfPieces(selected);
        const position = this.mesh && this.mesh.geometry.getAttribute('position');
        if (!vertices.length || !position) return null;
        const low = new THREE.Vector3(Infinity, Infinity, Infinity);
        const high = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
        for (const v of vertices) {
            low.min(new THREE.Vector3(position.getX(v), position.getY(v), position.getZ(v)));
            high.max(new THREE.Vector3(position.getX(v), position.getY(v), position.getZ(v)));
        }
        return low.add(high).multiplyScalar(0.5);
    }

    /* Put a drag handle on the chosen pieces. `onMoved` is called once, on release,
       with the total shift in model (Z-up) space, so the engine is told the answer
       rather than every step of the way. */
    beginMove(selected, onMoved) {
        this.endMove();
        const centre = this.centreOfPieces(selected);
        if (!centre) return false;

        this.moveVerts = this.verticesOfPieces(selected);
        this.moveHandle = new THREE.Object3D();
        this.moveHandle.position.copy(centre);
        this.scene.add(this.moveHandle);
        this.moveFrom = centre.clone();
        this.moveLast = centre.clone();

        if (!this.mover) {
            this.mover = new THREE.TransformControls(this.camera, this.renderer.domElement);
            this.mover.setMode('translate');
            this.mover.setSpace('world');
            this.mover.addEventListener('change', () => this.requestRender());
            this.mover.addEventListener('dragging-changed', e => { this.controls.enabled = !e.value; });
            this.mover.addEventListener('objectChange', () => this.dragPieces());
            this.mover.addEventListener('mouseUp', () => {
                const shift = this.moveHandle.position.clone().sub(this.moveFrom);
                // viewer (Y-up) back to model (Z-up): the geometry was turned a
                // quarter turn about X on the way in, so the shift turns back.
                if (this.onPieceMove) {
                    this.onPieceMove([shift.x, -shift.z, shift.y]);
                }
            });
            this.scene.add(this.mover.getHelper());
        }
        this.onPieceMove = onMoved;
        this.mover.attach(this.moveHandle);
        this.mover.size = 0.7;
        this.requestRender();
        return true;
    }

    dragPieces() {
        if (!this.moveHandle || !this.moveVerts || !this.mesh) return;
        const step = this.moveHandle.position.clone().sub(this.moveLast);
        if (step.lengthSq() === 0) return;
        this.moveLast.copy(this.moveHandle.position);
        const position = this.mesh.geometry.getAttribute('position');
        for (const v of this.moveVerts) {
            position.setXYZ(v, position.getX(v) + step.x,
                            position.getY(v) + step.y, position.getZ(v) + step.z);
        }
        position.needsUpdate = true;
        this.dropPickTree();
        this.mesh.geometry.computeBoundingSphere();
        // The open-edge overlay and the wireframe are built from the geometry as it
        // was; they catch up when the move is committed and the preview is rebuilt.
        this.requestRender();
    }

    endMove() {
        if (this.mover) this.mover.detach();
        if (this.moveHandle) {
            this.scene.remove(this.moveHandle);
            this.moveHandle = null;
        }
        this.moveVerts = null;
        this.onPieceMove = null;
        this.requestRender();
    }

    /* Rotate instantly about the model centre; axis is in model (Z-up) space. */
    rotateModel(axis, degrees) {
        if (!this.modelGroup) return;
        const map = { x: [1, 0, 0], y: [0, 0, -1], z: [0, 1, 0] };   // model axis -> viewer axis
        const v = new THREE.Vector3(...(map[axis] || map.z));
        const q = new THREE.Quaternion().setFromAxisAngle(v, THREE.MathUtils.degToRad(degrees));
        this.modelGroup.quaternion.premultiply(q);
        this.modelGroup.updateMatrixWorld(true);
        this.requestRender();
        if (this.onRotate) this.onRotate(this.modelMatrix());
    }

    /* Current group rotation expressed in model (Z-up) space, row-major 3x3. */
    modelMatrix() {
        const toModel = new THREE.Matrix4().makeRotationX(Math.PI / 2);
        const toViewer = new THREE.Matrix4().makeRotationX(-Math.PI / 2);
        const v = new THREE.Matrix4().makeRotationFromQuaternion(this.modelGroup.quaternion);
        const m = toModel.multiply(v).multiply(toViewer);
        const e = m.elements;   // column-major
        return [[e[0], e[4], e[8]], [e[1], e[5], e[9]], [e[2], e[6], e[10]]];
    }

    /* Called once the backend has baked the rotation into the mesh.
       `bounds` = backend [[minx,miny,minz],[maxx,maxy,maxz]] after the bake. */
    bakeGroupRotation(bounds) {
        if (!this.modelGroup || !this.mesh) return;
        const m = new THREE.Matrix4().makeRotationFromQuaternion(this.modelGroup.quaternion);
        for (const o of [this.mesh, this.wire, this.edgeLines]) {
            if (o) o.geometry.applyMatrix4(m);
        }
        this.dropPickTree();
        this.clearHighlight();
        this.modelGroup.quaternion.identity();
        this.modelGroup.updateMatrixWorld(true);

        // Backend bounds -> viewer space (x, z, -y), then the same recentering rule as loadGeometry.
        const [lo, hi] = bounds;
        const vMin = new THREE.Vector3(lo[0], lo[2], -hi[1]);
        const vMax = new THREE.Vector3(hi[0], hi[2], -lo[1]);
        this.modelOffset = new THREE.Vector3(-(vMin.x + vMax.x) / 2, -vMin.y, -(vMin.z + vMax.z) / 2);

        // Rotation happened about a different centre than the backend's; fix with a pure translation.
        const g = this.mesh.geometry;
        g.computeBoundingBox();
        const want = vMin.clone().add(this.modelOffset);
        const shift = want.sub(g.boundingBox.min);
        for (const o of [this.mesh, this.wire, this.edgeLines]) {
            if (o) o.geometry.translate(shift.x, shift.y, shift.z);
        }
        g.computeVertexNormals();
        g.computeBoundingBox();
        g.computeBoundingSphere();
        this.requestRender();
    }

    /* Small XYZ compass drawn from the camera orientation (model is Z-up). */
    drawCompass(ctx, size) {
        const c = size / 2, len = size * 0.36;
        ctx.clearRect(0, 0, size, size);
        const q = this.camera.quaternion.clone().invert();
        if (this.modelGroup) q.multiply(this.modelGroup.quaternion);
        const axes = [
            { n: 'X', v: new THREE.Vector3(1, 0, 0), col: '#e5534b' },
            { n: 'Y', v: new THREE.Vector3(0, 0, -1), col: '#4cc38a' },
            { n: 'Z', v: new THREE.Vector3(0, 1, 0), col: '#6ea8fe' },
        ];
        const proj = axes.map(a => { const p = a.v.clone().applyQuaternion(q); return { ...a, x: c + p.x * len, y: c - p.y * len, z: p.z }; });
        proj.sort((a, b) => a.z - b.z);
        ctx.lineWidth = 1.5;
        for (const a of proj) {
            ctx.strokeStyle = a.col; ctx.fillStyle = a.col;
            ctx.globalAlpha = a.z < 0 ? 0.45 : 1;
            ctx.beginPath(); ctx.moveTo(c, c); ctx.lineTo(a.x, a.y); ctx.stroke();
            ctx.beginPath(); ctx.arc(a.x, a.y, 6, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = '#111315'; ctx.font = 'bold 8px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText(a.n, a.x, a.y + 0.5);
        }
        ctx.globalAlpha = 1;
    }

    clear() {
        this.clearHighlight();
        if (this.gizmo) this.gizmo.detach();
        for (const o of [this.mesh, this.wire, this.edgeLines]) {
            if (!o) continue;
            if (o.parent) o.parent.remove(o);
            o.geometry.boundsTree = null;
            o.geometry.dispose();
        }
        if (this.modelGroup) {
            this.scene.remove(this.modelGroup);
            this.modelGroup = null;
        }
        this.mesh = this.wire = this.edgeLines = null;
        this.requestRender();
    }

    /* Full reset back to an empty viewport: geometry, PBR maps and shading mode.
       clear() alone leaves the previous model's textures on the shared materials,
       which would then appear on whatever is loaded next. */
    reset() {
        this.clear();
        this.clearTextures();
        this.shellMode = false;
        this.shellFaceCounts = null;
        this.modelOffset = null;
        this.setMode('shaded');
    }

    clearTextures() {
        this.textureData = null;
        const slots = ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'aoMap', 'displacementMap'];
        const disposed = new Set();
        for (const name of Object.keys(this.materials)) {
            const m = this.materials[name];
            for (const slot of slots) {
                const tex = m[slot];
                if (!tex) continue;
                if (!disposed.has(tex)) { tex.dispose(); disposed.add(tex); }
                m[slot] = null;
            }
            m.needsUpdate = true;
        }
        this.materials.pbr.roughness = 0.5;
        this.materials.pbr.metalness = 0.05;
        this.materials.pbr.displacementScale = 0;
        this.requestRender();
    }

    /* Bounding sphere in world space (the group may be rotated). */
    worldSphere() {
        const s = this.mesh.geometry.boundingSphere.clone();
        s.center.copy(this.modelGroup.localToWorld(s.center.clone()));
        return s;
    }

    fit() {
        if (!this.mesh) return;
        const s = this.worldSphere();
        const dist = s.radius / Math.sin((this.camera.fov * Math.PI / 180) / 2) * 1.15;
        this.camera.near = Math.max(0.01, dist / 500);
        this.camera.far = dist * 20;
        this.camera.updateProjectionMatrix();
        this.camera.position.set(s.center.x + dist * 0.6, s.center.y + dist * 0.5, s.center.z + dist * 0.75);
        this.controls.target.copy(s.center);
        this.controls.update();
        this.lightDist = s.radius * 4;
        this.updateLight();
        const gridSize = Math.max(100, Math.ceil(s.radius * 3 / 50) * 50);
        this.scene.remove(this.grid);
        this.grid.dispose();
        this.grid = ModelViewer.makeGrid(gridSize, Math.round(gridSize / 10));
        this.grid.visible = this.gridVisible !== false;
        this.scene.add(this.grid);
        this.requestRender();
    }

    setMode(mode) {
        if (!this.materials[mode]) return;
        this.mode = mode;
        if (this.mesh && !this.shellMode) this.mesh.material = this.materials[mode];
        this.requestRender();
    }
    buildWire() {
        if (this.wire || !this.mesh) return;
        this.wire = new THREE.LineSegments(new THREE.WireframeGeometry(this.mesh.geometry),
            new THREE.LineBasicMaterial({ color: linear(0x6b7280), transparent: true, opacity: 0.35 }));
        this.modelGroup.add(this.wire);
        this.requestRender();
    }

    toggleWire() {
        this.showWire = !this.showWire;
        if (this.showWire) this.buildWire();
        if (this.wire) this.wire.visible = this.showWire;
        this.requestRender();
        return this.showWire;
    }
    toggleEdges() { this.showEdges = !this.showEdges; if (this.edgeLines) this.edgeLines.visible = this.showEdges; this.requestRender(); return this.showEdges; }
    toggleGrid() { this.gridVisible = !(this.gridVisible !== false); this.grid.visible = this.gridVisible; this.requestRender(); return this.gridVisible; }

    updateLight() {
        const az = this.lightAz * Math.PI / 180, el = this.lightEl * Math.PI / 180;
        this.key.position.set(
            this.lightDist * Math.cos(el) * Math.sin(az),
            this.lightDist * Math.sin(el),
            this.lightDist * Math.cos(el) * Math.cos(az));
        this.requestRender();
    }
    setLight(az, el, power) {
        this.lightAz = az; this.lightEl = el; this.key.intensity = power * LIGHT_SCALE;
        this.updateLight();
        this.requestRender();
    }

    applyPBRTextures(textureMaps) {
        if (!textureMaps) return;
        this.textureData = textureMaps;
        const loader = new THREE.TextureLoader();

        const loadTex = (dataUri, isColor = false) => {
            if (!dataUri) return null;
            const tex = loader.load(dataUri, () => { this.requestRender(); });
            tex.wrapS = THREE.RepeatWrapping;
            tex.wrapT = THREE.RepeatWrapping;
            // Data maps (normal, roughness, ...) keep the default NoColorSpace.
            if (isColor) tex.colorSpace = THREE.SRGBColorSpace;
            return tex;
        };

        const albedoTex = loadTex(textureMaps.albedo, true);
        const normalTex = loadTex(textureMaps.normal, false);
        const roughnessTex = loadTex(textureMaps.roughness, false);
        const metallicTex = loadTex(textureMaps.metallic, false);
        const aoTex = loadTex(textureMaps.ao, false);
        const heightTex = loadTex(textureMaps.height, false);

        // Configure PBR material
        const pbr = this.materials.pbr;
        pbr.map = albedoTex;
        pbr.normalMap = normalTex;
        if (normalTex) pbr.normalScale = new THREE.Vector2(1.2, 1.2);
        pbr.roughnessMap = roughnessTex;
        pbr.roughness = roughnessTex ? 1.0 : 0.5;
        pbr.metalnessMap = metallicTex;
        pbr.metalness = metallicTex ? 1.0 : 0.05;
        pbr.aoMap = aoTex;
        pbr.aoMapIntensity = 1.0;
        // Displacement moves real vertices, so it only shows detail on a dense mesh.
        // Off by default; the panel slider turns it up in millimetres.
        pbr.displacementMap = heightTex;
        pbr.displacementScale = heightTex ? this.displaceMm : 0;
        pbr.displacementBias = heightTex ? -this.displaceMm / 2 : 0;
        pbr.needsUpdate = true;

        // Configure Channel Isolation materials
        if (albedoTex) this.materials.albedo.map = albedoTex;
        if (normalTex) this.materials.normal_map.map = normalTex;
        if (roughnessTex) this.materials.roughness_map.map = roughnessTex;
        if (metallicTex) this.materials.metallic_map.map = metallicTex;
        if (aoTex) this.materials.ao_map.map = aoTex;
        if (heightTex) this.materials.height_map.map = heightTex;

        for (const m of ['albedo', 'normal_map', 'roughness_map', 'metallic_map', 'ao_map', 'height_map']) {
            this.materials[m].needsUpdate = true;
        }

        if (this.mode === 'shaded') {
            this.setMode('pbr');
        } else if (this.mesh && this.materials[this.mode]) {
            this.mesh.material = this.materials[this.mode];
        }
        this.requestRender();
    }

    /* Height-map displacement, in millimetres of surface travel. */
    setDisplacement(mm) {
        this.displaceMm = Math.max(0, Number(mm) || 0);
        const pbr = this.materials.pbr;
        if (!pbr.displacementMap) return false;
        pbr.displacementScale = this.displaceMm;
        pbr.displacementBias = -this.displaceMm / 2;
        pbr.needsUpdate = true;
        this.requestRender();
        return true;
    }

    hasHeightMap() { return !!this.materials.pbr.displacementMap; }

    setEnvironment(rotationDeg = 0, exposure = 1.0) {
        this.renderer.toneMappingExposure = exposure;
        if (this.key) {
            const rad = (this.lightAz + rotationDeg) * Math.PI / 180, el = this.lightEl * Math.PI / 180;
            this.key.position.set(this.lightDist * Math.cos(el) * Math.sin(rad), this.lightDist * Math.sin(el), this.lightDist * Math.cos(el) * Math.cos(rad));
        }
        this.requestRender();
    }
}

window.viewer = null;
document.addEventListener('DOMContentLoaded', () => { window.viewer = new ModelViewer('webgl'); });

/* Meshwright viewport — Geekatplay Studio */
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

        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.setSize(w, h);
        this.renderer.outputEncoding = THREE.sRGBEncoding;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 1.0;
        this.container.appendChild(this.renderer.domElement);

        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.08;

        this.scene.add(new THREE.HemisphereLight(0xdfe6f0, 0x2a2420, 0.55));
        this.key = new THREE.DirectionalLight(0xfff4e0, 1.2);
        this.scene.add(this.key);
        this.fill = new THREE.DirectionalLight(0x9fb8d8, 0.35);
        this.fill.position.set(-200, 80, -150);
        this.scene.add(this.fill);
        this.updateLight();

        this.grid = new THREE.GridHelper(256, 16, 0x3a3f47, 0x22262b);
        this.scene.add(this.grid);

        this.materials = {
            shaded: new THREE.MeshStandardMaterial({ color: 0xb9bec6, roughness: 0.55, metalness: 0.05, side: THREE.DoubleSide }),
            clay: new THREE.MeshStandardMaterial({ color: 0xd8b98a, roughness: 0.9, metalness: 0.0, side: THREE.DoubleSide }),
            normals: new THREE.MeshNormalMaterial({ side: THREE.DoubleSide }),
            xray: new THREE.MeshBasicMaterial({ color: 0x9fc4ff, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false }),
            shells: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.92, metalness: 0.0, side: THREE.DoubleSide }),
        };

        new ResizeObserver(() => this.resize()).observe(this.container);
        this.animate();
    }

    animate() {
        requestAnimationFrame(() => this.animate());
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
        if (this.compassCtx) this.drawCompass(this.compassCtx, 72);
    }

    resize() {
        const w = this.container.clientWidth, h = this.container.clientHeight;
        if (!w || !h) return;
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(w, h);
    }

    /* Load indexed geometry: Float32 vertices, Uint32 faces, Uint32 boundary edge pairs. */
    loadGeometry(preview, shellFaceCounts) {
        this.clear();
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
            this.edgeLines = new THREE.LineSegments(eg, new THREE.LineBasicMaterial({ color: 0xe5534b, depthTest: false }));
            this.edgeLines.renderOrder = 10;
            this.edgeLines.visible = this.showEdges;
            this.modelGroup.add(this.edgeLines);
        }
        if (this.gizmoOn) this.attachGizmo(true);
        this.fit();
        return bEdges.length / 2;
    }

    /* Selection is red and issue highlights are cyan, so the shell palette
       avoids both: blue, violet, green, amber, magenta, lime, indigo, orange. */
    static shellColor(i, selected) {
        const c = new THREE.Color();
        if (selected) return c.setHex(0xe5534b);
        const base = [0.62, 0.78, 0.36, 0.12, 0.88, 0.24, 0.70, 0.07];
        const hue = base[i % base.length];
        // Deep and saturated: a large piece under the key light washes out otherwise.
        const light = 0.44 - 0.08 * (Math.floor(i / base.length) % 3);
        c.setHSL(hue, 0.72, light);
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
    }

    hideShells() {
        this.shellMode = false;
        if (this.mesh) this.mesh.material = this.materials[this.mode];
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
        const pts = location.points.map(p => this.toViewer(p));
        const radius = this.mesh.geometry.boundingSphere.radius;
        const group = new THREE.Group();

        const arr = new Float32Array(pts.length * 3);
        pts.forEach((v, i) => { arr[i * 3] = v.x; arr[i * 3 + 1] = v.y; arr[i * 3 + 2] = v.z; });
        const pg = new THREE.BufferGeometry();
        pg.setAttribute('position', new THREE.BufferAttribute(arr, 3));
        const dots = new THREE.Points(pg, new THREE.PointsMaterial({ color: 0x22d3ee, size: 8, sizeAttenuation: false, depthTest: false }));
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
            new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.10, depthWrite: false }));
        sphere.position.copy(center);
        group.add(sphere);
        const ring = new THREE.Mesh(new THREE.SphereGeometry(areaR, 24, 16),
            new THREE.MeshBasicMaterial({ color: 0x22d3ee, wireframe: true, transparent: true, opacity: 0.35, depthTest: false }));
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
    }

    /* Interactive rotation gizmo. Rotation happens instantly in the viewport;
       the backend is synced afterwards via onRotate (no geometry round-trip). */
    attachGizmo(on) {
        this.gizmoOn = on;
        if (!this.gizmo) {
            this.gizmo = new THREE.TransformControls(this.camera, this.renderer.domElement);
            this.gizmo.setMode('rotate');
            this.gizmo.setSpace('world');
            this.gizmo.addEventListener('dragging-changed', e => { this.controls.enabled = !e.value; });
            this.gizmo.addEventListener('mouseUp', () => {
                if (this.onRotate) this.onRotate(this.modelMatrix());
            });
            this.scene.add(this.gizmo);
        }
        if (on && this.modelGroup) {
            this.gizmo.attach(this.modelGroup);
            this.gizmo.size = 0.8;
        } else {
            this.gizmo.detach();
        }
        return this.gizmoOn;
    }

    /* Rotate instantly about the model centre; axis is in model (Z-up) space. */
    rotateModel(axis, degrees) {
        if (!this.modelGroup) return;
        const map = { x: [1, 0, 0], y: [0, 0, -1], z: [0, 1, 0] };   // model axis -> viewer axis
        const v = new THREE.Vector3(...(map[axis] || map.z));
        const q = new THREE.Quaternion().setFromAxisAngle(v, THREE.MathUtils.degToRad(degrees));
        this.modelGroup.quaternion.premultiply(q);
        this.modelGroup.updateMatrixWorld(true);
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
            o.geometry.dispose();
        }
        if (this.modelGroup) {
            this.scene.remove(this.modelGroup);
            this.modelGroup = null;
        }
        this.mesh = this.wire = this.edgeLines = null;
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
        this.grid = new THREE.GridHelper(gridSize, Math.round(gridSize / 10), 0x3a3f47, 0x22262b);
        this.grid.visible = this.gridVisible !== false;
        this.scene.add(this.grid);
    }

    setMode(mode) {
        if (!this.materials[mode]) return;
        this.mode = mode;
        if (this.mesh && !this.shellMode) this.mesh.material = this.materials[mode];
    }
    buildWire() {
        if (this.wire || !this.mesh) return;
        this.wire = new THREE.LineSegments(new THREE.WireframeGeometry(this.mesh.geometry),
            new THREE.LineBasicMaterial({ color: 0x6b7280, transparent: true, opacity: 0.35 }));
        this.modelGroup.add(this.wire);
    }

    toggleWire() {
        this.showWire = !this.showWire;
        if (this.showWire) this.buildWire();
        if (this.wire) this.wire.visible = this.showWire;
        return this.showWire;
    }
    toggleEdges() { this.showEdges = !this.showEdges; if (this.edgeLines) this.edgeLines.visible = this.showEdges; return this.showEdges; }
    toggleGrid() { this.gridVisible = !(this.gridVisible !== false); this.grid.visible = this.gridVisible; return this.gridVisible; }

    updateLight() {
        const az = this.lightAz * Math.PI / 180, el = this.lightEl * Math.PI / 180;
        this.key.position.set(
            this.lightDist * Math.cos(el) * Math.sin(az),
            this.lightDist * Math.sin(el),
            this.lightDist * Math.cos(el) * Math.cos(az));
    }
    setLight(az, el, power) {
        this.lightAz = az; this.lightEl = el; this.key.intensity = power;
        this.updateLight();
    }
}

window.viewer = null;
document.addEventListener('DOMContentLoaded', () => { window.viewer = new ModelViewer('webgl'); });

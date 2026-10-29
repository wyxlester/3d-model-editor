// three.js viewers: ship (pins, picking, highlight, joint preview), crew (animation), and the
// building blocks Assembly and Review reuse. glTF is Y-up; the ship's waterline is y = 0.
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Canvas, createPortal, useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { Html, OrbitControls, useAnimations, useGLTF } from '@react-three/drei';
import * as THREE from 'three';
import { clone as skeletonClone } from 'three/examples/jsm/utils/SkeletonUtils.js';
import { cameraFor, jointAxisGltf, type Bounds, type ViewName } from '../lib/frames';
import { isStationName, ownNodes, partLabel, resolvePart } from '../lib/parts';
import { convexHull, waterlineCrossings, type Pt } from '../lib/waterline';
import type { Pin, Vec3 } from '../types';
import { ErrorBoundary } from '../components/common';

export type JointPreview = Record<string, { axis: string; deg: number }>;

export interface PickResult {
  part: string | null;
  point: Vec3;
  clientX: number;
  clientY: number;
}

interface ControlsLike { target: THREE.Vector3; update(): void; autoRotate: boolean; autoRotateSpeed: number }

function CameraRig({ view, bounds, nonce }: { view: ViewName; bounds: Bounds | null; nonce: number }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const controls = useThree((s) => s.controls) as unknown as ControlsLike | null;
  const size = useThree((s) => s.size);
  useEffect(() => {
    if (!bounds) return;
    const { position, target } = cameraFor(view, bounds, camera.fov, size.width / Math.max(1, size.height));
    camera.position.set(...position);
    const d = camera.position.distanceTo(new THREE.Vector3(...target));
    camera.near = Math.max(0.01, d / 500);
    camera.far = d * 50;
    camera.updateProjectionMatrix();
    if (controls) {
      controls.target.set(...target);
      controls.autoRotate = view === 'orbit';
      controls.autoRotateSpeed = 0.8;
      controls.update();
    } else {
      camera.lookAt(...target);
    }
    // size is deliberately not a dependency: resizing should not reset the user's camera.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, bounds, nonce, controls]);
  return null;
}

// Stencil value inside the hull's waterline footprint: the water and its grid are not drawn there,
// so an open boat (the sampan's floorboards sit below the waterline) does not look flooded.
const IN_HULL = 1;

function Water({ bounds }: { bounds: Bounds | null }) {
  const span = bounds ? Math.max(bounds.max[0] - bounds.min[0], bounds.max[2] - bounds.min[2]) : 20;
  const size = Math.max(20, span * 6);
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position-y={0} renderOrder={2}>
        <planeGeometry args={[size, size]} />
        <meshStandardMaterial color="#1f5fb8" transparent opacity={0.28} depthWrite={false} side={THREE.DoubleSide}
          stencilWrite stencilRef={IN_HULL} stencilFunc={THREE.NotEqualStencilFunc} />
      </mesh>
      <gridHelper args={[size, 40, '#3F7BD8', '#22324a']} position-y={0.002}
        material-stencilWrite material-stencilRef={IN_HULL} material-stencilFunc={THREE.NotEqualStencilFunc} />
    </group>
  );
}

/** The hull's outline at the waterline, from the Hull__* parts' own meshes (every mesh if none is named so). */
function hullFootprint(root: THREE.Object3D, partPaths: string[]): Pt[] {
  root.updateMatrixWorld(true);
  const hullParts = partPaths.filter((p) => p.startsWith('Hull__'));
  const nodes: THREE.Object3D[] = [];
  if (hullParts.length) {
    for (const p of hullParts) { const o = root.getObjectByName(p); if (o) nodes.push(...ownNodes(o, partPaths)); }
  } else {
    root.traverse((o) => nodes.push(o));
  }
  const pts: Pt[] = [];
  const v = new THREE.Vector3();
  for (const o of nodes) {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) continue;
    const attr = mesh.geometry.getAttribute('position');
    const world = new Float32Array(attr.count * 3);
    for (let i = 0; i < attr.count; i++) {
      v.fromBufferAttribute(attr, i).applyMatrix4(mesh.matrixWorld);
      world[i * 3] = v.x; world[i * 3 + 1] = v.y; world[i * 3 + 2] = v.z;
    }
    for (const pt of waterlineCrossings(world, mesh.geometry.index?.array ?? null)) pts.push(pt);
  }
  return convexHull(pts);
}

/** Marks the hull's footprint in the stencil buffer, drawn before the water; invisible and not pickable. */
function WaterMask({ outline }: { outline: Pt[] }) {
  const geometry = useMemo(() => {
    if (outline.length < 3) return null;
    const pos: number[] = [];
    for (let i = 1; i + 1 < outline.length; i++) {
      for (const [x, z] of [outline[0], outline[i], outline[i + 1]]) pos.push(x, 0, z);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    return g;
  }, [outline]);
  useEffect(() => () => geometry?.dispose(), [geometry]);
  if (!geometry) return null;
  return (
    <mesh geometry={geometry} renderOrder={-1} raycast={() => null}>
      <meshBasicMaterial colorWrite={false} depthWrite={false} depthTest={false} side={THREE.DoubleSide}
        stencilWrite stencilRef={IN_HULL} stencilFunc={THREE.AlwaysStencilFunc} stencilZPass={THREE.ReplaceStencilOp} />
    </mesh>
  );
}

function Lights() {
  return (
    <>
      <hemisphereLight args={['#dfe8f5', '#1a1d22', 1.4]} />
      <directionalLight position={[30, 50, -20]} intensity={2.2} />
      <directionalLight position={[-30, 20, 30]} intensity={0.6} />
    </>
  );
}

function boundsOf(o: THREE.Object3D): Bounds | null {
  const b = new THREE.Box3().setFromObject(o);
  if (b.isEmpty()) return null;
  return { min: [b.min.x, b.min.y, b.min.z], max: [b.max.x, b.max.y, b.max.z] };
}

/** Clone a loaded scene so several viewers (and highlight state) never share objects or materials. */
function useClonedScene(url: string) {
  const gltf = useGLTF(url);
  const root = useMemo(() => {
    const r = skeletonClone(gltf.scene);
    r.traverse((o) => {
      o.userData.restQ = o.quaternion.clone();
      const m = o as THREE.Mesh;
      if (m.isMesh) m.material = Array.isArray(m.material) ? m.material.map((x) => x.clone()) : m.material.clone();
    });
    return r;
  }, [gltf]);
  return { root, animations: gltf.animations };
}

function stdMaterialsOf(o: THREE.Object3D, fn: (m: THREE.MeshStandardMaterial) => void) {
  const mesh = o as THREE.Mesh;
  if (!mesh.isMesh) return;
  const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  for (const mat of mats) if ((mat as THREE.MeshStandardMaterial).emissive) fn(mat as THREE.MeshStandardMaterial);
}

function forEachStdMaterial(o: THREE.Object3D, fn: (m: THREE.MeshStandardMaterial) => void) {
  o.traverse((c) => stdMaterialsOf(c, fn));
}

export interface ShipModelProps {
  url: string;
  partPaths: string[];
  pickMode?: boolean;
  onPick?: (r: PickResult) => void;
  onHover?: (part: string | null) => void;
  selectedPart?: string | null;
  joints?: JointPreview;
  pins?: Pin[];
  selectedPinId?: string | null;
  onPinClick?: (pin: Pin, clientX: number, clientY: number) => void;
  onBounds?: (b: Bounds | null) => void;
  children?: (root: THREE.Object3D) => ReactNode;
}

function ShipModel(p: ShipModelProps) {
  const { root } = useClonedScene(p.url);
  const [hover, setHover] = useState<string | null>(null);
  const onBounds = p.onBounds;
  useEffect(() => { onBounds?.(boundsOf(root)); }, [root, onBounds]);
  const partsKey = p.partPaths.join('\n'); // some screens pass a fresh array every render
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const footprint = useMemo(() => hullFootprint(root, p.partPaths), [root, partsKey]);

  // Highlight: selected part strongly, hovered part (while placing a comment) lightly.
  useEffect(() => {
    forEachStdMaterial(root, (m) => {
      if (m.userData.baseEmissive === undefined) {
        m.userData.baseEmissive = m.emissive.getHex();
        m.userData.baseIntensity = m.emissiveIntensity;
      }
      m.emissive.setHex(m.userData.baseEmissive);
      m.emissiveIntensity = m.userData.baseIntensity;
    });
    // Only the part's own meshes: the parts attached under it (the hull carries the whole ship) stay unlit.
    const tint = (name: string | null | undefined, k: number) => {
      const o = name ? root.getObjectByName(name) : null;
      if (!o) return;
      for (const n of ownNodes(o, p.partPaths)) stdMaterialsOf(n, (m) => { m.emissive.set('#5B9BFF'); m.emissiveIntensity = k; });
    };
    tint(p.pickMode ? hover : null, 0.25);
    tint(p.selectedPart, 0.55);
  }, [root, p.selectedPart, hover, p.pickMode, p.partPaths]);

  // Joint preview: rotate each articulated node about its joint axis from its rest pose.
  useEffect(() => {
    root.traverse((o) => {
      if (o.userData.jointPreviewed) { o.quaternion.copy(o.userData.restQ); o.userData.jointPreviewed = false; }
    });
    for (const [name, { axis, deg }] of Object.entries(p.joints ?? {})) {
      const o = root.getObjectByName(name);
      if (!o) continue;
      const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(...jointAxisGltf(axis)), THREE.MathUtils.degToRad(deg));
      o.quaternion.copy(o.userData.restQ).multiply(q);
      o.userData.jointPreviewed = true;
    }
  }, [root, p.joints]);

  const chainOf = (o: THREE.Object3D | null) => {
    const chain: string[] = [];
    for (let c = o; c; c = c.parent) chain.push(c.name);
    return chain;
  };
  const onClick = (e: ThreeEvent<MouseEvent>) => {
    if (!p.pickMode || e.delta > 4) return; // a drag to orbit is not a click
    e.stopPropagation();
    p.onPick?.({
      part: resolvePart(chainOf(e.object), p.partPaths),
      point: [e.point.x, e.point.y, e.point.z],
      clientX: e.nativeEvent.clientX, clientY: e.nativeEvent.clientY,
    });
  };
  const onMove = (e: ThreeEvent<PointerEvent>) => {
    if (!p.pickMode) return;
    e.stopPropagation();
    const part = resolvePart(chainOf(e.object), p.partPaths);
    if (part !== hover) { setHover(part); p.onHover?.(part); }
  };
  const onOut = () => { if (hover) { setHover(null); p.onHover?.(null); } };

  return (
    <>
      <primitive object={root} onClick={onClick} onPointerMove={onMove} onPointerOut={onOut} />
      <WaterMask outline={footprint} />
      {p.pins?.map((pin, i) => (
        <PinMarker key={pin.id} root={root} pin={pin} n={i + 1} selected={pin.id === p.selectedPinId} onClick={p.onPinClick} />
      ))}
      {p.children?.(root)}
    </>
  );
}

export const PIN_COLORS: Record<string, string> = { resolved: '#3D8A66', open: '#E6B04A', working: '#5B9BFF', waiting: '#5B9BFF' };

function PinMarker({ root, pin, n, selected, onClick }: {
  root: THREE.Object3D; pin: Pin; n: number; selected: boolean; onClick?: (pin: Pin, x: number, y: number) => void;
}) {
  const target = useMemo(() => root.getObjectByName(pin.part) ?? null, [root, pin.part]);
  // Anchor in the part's local frame so the pin follows the part (joint previews, rebuilds).
  const local = useMemo(() => {
    const v = new THREE.Vector3(...pin.point);
    if (target) { root.updateMatrixWorld(true); target.worldToLocal(v); }
    return v;
  }, [root, target, pin.point]);
  const el = (
    <group position={local}>
      <Html zIndexRange={[30, 10]}>
        <button
          className={`pin ${selected ? 'sel' : ''}`}
          style={{ background: PIN_COLORS[pin.status] ?? '#A7AEB8', opacity: target ? 1 : 0.6 }}
          aria-label={`Comment ${n} on ${partLabel(pin.part)}`}
          title={target ? undefined : `${pin.part} is not in this version`}
          onClick={(e) => { e.stopPropagation(); onClick?.(pin, e.clientX, e.clientY); }}
        >{n}</button>
      </Html>
    </group>
  );
  return target ? createPortal(el, target) : el;
}

/**
 * Reports loading / error text to the DOM overlay outside the Canvas. drei's <Html> is not used for
 * Suspense fallbacks: unmounting it while Suspense resolves crashed the whole r3f tree (React 19).
 */
function Signal({ text, set }: { text: string | null; set: (t: string | null) => void }) {
  useEffect(() => { set(text); }, [text, set]);
  return null;
}

/** Ship viewer with water, views, pins, picking and an overlay slot for popovers. */
export function ShipViewer(props: Omit<ShipModelProps, 'url' | 'onBounds'> & {
  url: string | null;
  view: ViewName;
  viewNonce?: number;
  overlay?: ReactNode;
  emptyText?: string;
  containerRef?: React.Ref<HTMLDivElement>;
}) {
  const [bounds, setBoundsRaw] = useState<Bounds | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const setBounds = useCallback((b: Bounds | null) => { setBoundsRaw(b); setStatus(null); }, []);
  const { url, view, viewNonce = 0, overlay, emptyText, containerRef, ...model } = props;
  return (
    <div className={`viewer ${props.pickMode ? 'picking' : ''}`} ref={containerRef}>
      <Canvas camera={{ fov: 40, position: [30, 15, -30] }} dpr={[1, 2]} gl={{ stencil: true }} style={{ position: 'absolute', inset: 0 }}>
        <color attach="background" args={['#0E1013']} />
        <Lights />
        <Water bounds={bounds} />
        {url && (
          <ErrorBoundary resetKey={url} fallback={(e) => <Signal text={`Couldn't load the model: ${e.message}`} set={setStatus} />}>
            <Suspense fallback={<Signal text="Loading model…" set={setStatus} />}>
              <ShipModel url={url} onBounds={setBounds} {...model} />
            </Suspense>
          </ErrorBoundary>
        )}
        <OrbitControls makeDefault enableDamping dampingFactor={0.12} />
        <CameraRig view={view} bounds={bounds} nonce={viewNonce} />
      </Canvas>
      {!url && <div className="viewer-msg">{emptyText ?? 'No model yet.'}</div>}
      {url && status && <div className="viewer-msg">{status === 'Loading model…' && <span className="spinner" style={{ marginRight: 8 }} />}{status}</div>}
      {overlay}
    </div>
  );
}

export const VIEWS: { id: ViewName; label: string }[] = [
  { id: 'port', label: 'Port' },
  { id: 'starboard', label: 'Starboard' },
  { id: 'top', label: 'Top' },
  { id: 'bow', label: 'Bow' },
  { id: 'orbit', label: 'Orbit' },
];

export function ViewButtons({ view, onView }: { view: ViewName; onView: (v: ViewName) => void }) {
  return (
    <span style={{ display: 'flex', gap: 6 }} role="group" aria-label="Camera view">
      {VIEWS.map((v) => (
        <button key={v.id} className={`btn ${view === v.id ? 'selected' : 'muted'}`} onClick={() => onView(v.id)}>{v.label}</button>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Crew
// ---------------------------------------------------------------------------

export interface CrewPlayback {
  animation: string | null;
  playing: boolean;
  /** Set to scrub: the clip is moved to `seek.t` seconds whenever `seek` changes identity. */
  seek?: { t: number } | null;
  onTime?: (t: number) => void;
  onDuration?: (d: number, clips: string[]) => void;
}

export function CrewModel({ url, animation, playing, seek, onTime, onDuration, position, scale }: CrewPlayback & {
  url: string; position?: Vec3; scale?: number;
}) {
  const { root, animations } = useClonedScene(url);
  const group = useRef<THREE.Group>(null);
  const { actions, mixer } = useAnimations(animations, group);
  const current = animation ? actions[animation] ?? null : null;
  const last = useRef(0);

  useEffect(() => {
    Object.values(actions).forEach((a) => a?.stop());
    if (current) { current.reset(); current.play(); }
    onDuration?.(current ? current.getClip().duration : 0, animations.map((a) => a.name));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, actions]);
  useEffect(() => { if (current) current.paused = !playing; }, [current, playing]);
  useEffect(() => {
    if (!current || !seek) return;
    current.time = Math.max(0, Math.min(seek.t, current.getClip().duration));
    mixer.update(0);
  }, [seek, current, mixer]);
  useFrame(({ clock }) => {
    if (!onTime || !current) return;
    if (clock.elapsedTime - last.current > 0.1) { last.current = clock.elapsedTime; onTime(current.time); }
  });

  return (
    <group ref={group} position={position} scale={scale ?? 1}>
      <primitive object={root} />
    </group>
  );
}

/** One or more crew members side by side on a deck that can roll (Moves: "all body sizes", "rolling deck"). */
export function CrewViewer({ urls, rollDeg = 0, ...playback }: CrewPlayback & { urls: string[]; rollDeg?: number }) {
  const [status, setStatus] = useState<string | null>(null);
  const n = Math.max(1, urls.length);
  const spacing = 1.2;
  const width = (n - 1) * spacing;
  const bounds: Bounds = { min: [-0.6, 0, -width / 2 - 0.6], max: [0.6, 1.95, width / 2 + 0.6] };
  return (
    <div className="viewer">
      <Canvas camera={{ fov: 35, position: [4, 1.5, 0] }} dpr={[1, 2]} style={{ position: 'absolute', inset: 0 }}>
        <color attach="background" args={['#0E1013']} />
        <Lights />
        <group rotation-x={THREE.MathUtils.degToRad(rollDeg)}>
          <mesh rotation-x={-Math.PI / 2}>
            <planeGeometry args={[4, Math.max(4, width + 3)]} />
            <meshStandardMaterial color="#1A1D22" />
          </mesh>
          {urls.map((u, i) => (
            <ErrorBoundary key={`${u}-${i}`} resetKey={u} fallback={(e) => <Signal text={`Couldn't load a crew model: ${e.message}`} set={setStatus} />}>
              <Suspense fallback={<Signal text="Loading crew member…" set={setStatus} />}>
                <Signal text={null} set={setStatus} />
                <CrewModel url={u} position={[0, 0, -width / 2 + i * spacing]} {...playback}
                  onTime={i === 0 ? playback.onTime : undefined} onDuration={i === 0 ? playback.onDuration : undefined} />
              </Suspense>
            </ErrorBoundary>
          ))}
        </group>
        <OrbitControls makeDefault enableDamping target={[0, 1, 0]} />
        <CameraRig view="bow" bounds={bounds} nonce={n} />
      </Canvas>
      {urls.length === 0 && <div className="viewer-msg">No model yet.</div>}
      {urls.length > 0 && status && <div className="viewer-msg">{status}</div>}
    </div>
  );
}

/** Station empties (`station__*`) under a loaded ship, for Assembly. */
export function stationObjects(root: THREE.Object3D): THREE.Object3D[] {
  const out: THREE.Object3D[] = [];
  root.traverse((o) => { if (isStationName(o.name)) out.push(o); });
  return out;
}

/** Render children attached to a node, so they follow it (crew riding an articulated part). */
export function AttachTo({ node, children }: { node: THREE.Object3D; children: ReactNode }) {
  return createPortal(<group>{children}</group>, node);
}

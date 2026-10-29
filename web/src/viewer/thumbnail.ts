// Still pictures of built models, for the project pickers. One offscreen renderer draws one model
// at a time (a page of live 3D views would run out of WebGL contexts), and each picture is cached
// by model URL: a URL names one version, so a cached picture never goes stale.
import { useEffect, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { cameraFor, type ViewName } from '../lib/frames';

const W = 320;
const H = 200;
const SHIP_ZOOM = 0.65;
const cache = new Map<string, Promise<string>>();
let queue: Promise<unknown> = Promise.resolve();
let renderer: THREE.WebGLRenderer | null = null;

/** A PNG data URL of the model at `url`, seen from `view`. Failures are not cached, so a later visit retries. */
export function thumbnail(url: string, view: ViewName): Promise<string> {
  const key = `${view}|${url}`;
  let picture = cache.get(key);
  if (!picture) {
    picture = queue.then(() => draw(url, view));
    queue = picture.catch(() => undefined);
    picture.catch(() => cache.delete(key));
    cache.set(key, picture);
  }
  return picture;
}

/** The picture for `url`, or null while it is drawn; `failed` when the model could not be drawn. */
export function useThumbnail(url: string | null, view: ViewName): { src: string | null; failed: boolean } {
  const [state, setState] = useState<{ key: string; src: string | null; failed: boolean }>({ key: '', src: null, failed: false });
  const key = url ? `${view}|${url}` : '';
  useEffect(() => {
    if (!url) return;
    let alive = true;
    thumbnail(url, view).then(
      (src) => { if (alive) setState({ key, src, failed: false }); },
      () => { if (alive) setState({ key, src: null, failed: true }); },
    );
    return () => { alive = false; };
  }, [url, view, key]);
  return state.key === key ? state : { src: null, failed: false };
}

async function draw(url: string, view: ViewName): Promise<string> {
  const gltf = await new GLTFLoader().loadAsync(url);
  try {
    if (!renderer) {
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(2);
      renderer.setSize(W, H, false);
      renderer.toneMapping = THREE.ACESFilmicToneMapping; // as the live viewer (react-three-fiber's default)
    }
    const scene = new THREE.Scene();
    scene.background = new THREE.Color('#0E1013');
    // The live viewer's lights (Viewer.tsx), so a picture looks like what opens.
    scene.add(new THREE.HemisphereLight('#dfe8f5', '#1a1d22', 1.4));
    const keyLight = new THREE.DirectionalLight('#ffffff', 2.2);
    keyLight.position.set(30, 50, -20);
    const fill = new THREE.DirectionalLight('#ffffff', 0.6);
    fill.position.set(-30, 20, 30);
    scene.add(keyLight, fill, gltf.scene);
    // People stand in the first frame of their idle move, as in the live preview, not the T-pose they are rigged in.
    const idle = gltf.animations.find((a) => a.name === 'idle') ?? gltf.animations[0];
    if (idle) {
      const mixer = new THREE.AnimationMixer(gltf.scene);
      mixer.clipAction(idle).play();
      mixer.update(0);
      gltf.scene.updateMatrixWorld(true);
    }

    const box = new THREE.Box3().setFromObject(gltf.scene);
    if (box.isEmpty()) throw new Error('The model has nothing to draw');
    const { position, target } = cameraFor(view, { min: box.min.toArray(), max: box.max.toArray() }, 35, W / H);
    const camera = new THREE.PerspectiveCamera(35, W / H);
    // cameraFor fits the whole bounding sphere, which leaves a long ship small in a small picture:
    // move in closer. A standing person already fills the frame top to bottom.
    const zoom = view === 'orbit' ? SHIP_ZOOM : 1;
    camera.position.set(...position).sub(new THREE.Vector3(...target)).multiplyScalar(zoom).add(new THREE.Vector3(...target));
    camera.lookAt(...target);
    const d = camera.position.distanceTo(new THREE.Vector3(...target));
    camera.near = Math.max(0.01, d / 500);
    camera.far = d * 50;
    camera.updateProjectionMatrix();

    renderer.render(scene, camera);
    return renderer.domElement.toDataURL('image/png'); // read straight after drawing, before the buffer is cleared
  } finally {
    gltf.scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      mesh.geometry?.dispose();
      for (const m of ([] as THREE.Material[]).concat(mesh.material ?? [])) {
        for (const v of Object.values(m)) if (v instanceof THREE.Texture) v.dispose();
        m.dispose();
      }
    });
  }
}

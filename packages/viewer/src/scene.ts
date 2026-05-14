import {
  Clock,
  Color,
  PerspectiveCamera,
  Scene,
  WebGLRenderer,
  type WebGLRendererParameters,
} from 'three';

import type { ImageGardenViewerOptions } from './types';

export interface SceneHost {
  scene: Scene;
  camera: PerspectiveCamera;
  renderer: WebGLRenderer;
  clock: Clock;
  destroy(): void;
}

export function createSceneHost(
  container: HTMLElement,
  options: ImageGardenViewerOptions = {},
): SceneHost {
  const scene = new Scene();
  scene.background = new Color(options.backgroundColor ?? 0x05050a);

  const cameraOptions = options.camera ?? {};
  const camera = new PerspectiveCamera(
    cameraOptions.fov ?? 70,
    getAspect(container),
    cameraOptions.near ?? 0.1,
    cameraOptions.far ?? 10_000,
  );
  const cameraPosition = cameraOptions.position ?? [0, 0, 180];
  camera.position.set(cameraPosition[0], cameraPosition[1], cameraPosition[2]);

  const rendererParameters: WebGLRendererParameters = {
    antialias: true,
    alpha: false,
    powerPreference: 'high-performance',
    ...(options.renderer ?? {}),
  };
  const renderer = new WebGLRenderer(rendererParameters);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(container.clientWidth || window.innerWidth, container.clientHeight || window.innerHeight);
  renderer.domElement.style.display = 'block';
  renderer.domElement.style.width = '100%';
  renderer.domElement.style.height = '100%';
  renderer.domElement.tabIndex = 0;
  container.appendChild(renderer.domElement);

  const resize = (): void => {
    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || window.innerHeight;
    camera.aspect = width / Math.max(height, 1);
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
  };

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(container);
  window.addEventListener('resize', resize);
  resize();

  return {
    scene,
    camera,
    renderer,
    clock: new Clock(),
    destroy() {
      resizeObserver.disconnect();
      window.removeEventListener('resize', resize);
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

function getAspect(container: HTMLElement): number {
  const width = container.clientWidth || window.innerWidth || 1;
  const height = container.clientHeight || window.innerHeight || 1;
  return width / height;
}

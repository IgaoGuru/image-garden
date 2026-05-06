import { Vector3, type PerspectiveCamera } from 'three';
import { PointerLockControls } from 'three/examples/jsm/controls/PointerLockControls.js';

import type { ControlsOptions } from './types';

interface KeyState {
  forward: boolean;
  backward: boolean;
  left: boolean;
  right: boolean;
  sprint: boolean;
}

export interface FlyControls {
  readonly pointer: PointerLockControls;
  update(deltaSeconds: number): void;
  lock(): void;
  unlock(): void;
  destroy(): void;
}

export function createFlyControls(
  camera: PerspectiveCamera,
  domElement: HTMLElement,
  options: ControlsOptions = {},
): FlyControls {
  const enabled = options.enabled ?? true;
  const clickToLock = options.clickToLock ?? true;
  const moveSpeed = options.moveSpeed ?? 45;
  const sprintMultiplier = options.sprintMultiplier ?? 3;
  const pointer = new PointerLockControls(camera, domElement);
  const forwardDirection = new Vector3();
  const rightDirection = new Vector3();
  const movement = new Vector3();

  const keys: KeyState = {
    forward: false,
    backward: false,
    left: false,
    right: false,
    sprint: false,
  };

  const setKey = (event: KeyboardEvent, pressed: boolean): void => {
    switch (event.code) {
      case 'KeyW':
      case 'ArrowUp':
        keys.forward = pressed;
        break;
      case 'KeyS':
      case 'ArrowDown':
        keys.backward = pressed;
        break;
      case 'KeyA':
      case 'ArrowLeft':
        keys.left = pressed;
        break;
      case 'KeyD':
      case 'ArrowRight':
        keys.right = pressed;
        break;
      case 'ShiftLeft':
      case 'ShiftRight':
        keys.sprint = pressed;
        break;
      default:
        return;
    }
    event.preventDefault();
  };

  const onKeyDown = (event: KeyboardEvent): void => setKey(event, true);
  const onKeyUp = (event: KeyboardEvent): void => setKey(event, false);
  const onClick = (): void => {
    if (enabled && clickToLock && document.pointerLockElement !== domElement) {
      pointer.lock();
    }
  };

  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
  domElement.addEventListener('click', onClick);

  return {
    pointer,
    update(deltaSeconds: number): void {
      if (!enabled) return;
      const speed = moveSpeed * (keys.sprint ? sprintMultiplier : 1) * deltaSeconds;
      camera.updateMatrixWorld(true);
      camera.getWorldDirection(forwardDirection).normalize();
      rightDirection.setFromMatrixColumn(camera.matrixWorld, 0).normalize();

      movement.set(0, 0, 0);
      if (keys.forward) movement.add(forwardDirection);
      if (keys.backward) movement.sub(forwardDirection);
      if (keys.right) movement.add(rightDirection);
      if (keys.left) movement.sub(rightDirection);
      if (movement.lengthSq() > 0) {
        camera.position.addScaledVector(movement.normalize(), speed);
      }
    },
    lock(): void {
      if (enabled) pointer.lock();
    },
    unlock(): void {
      pointer.unlock();
    },
    destroy(): void {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      domElement.removeEventListener('click', onClick);
      pointer.unlock();
      pointer.dispose();
    },
  };
}

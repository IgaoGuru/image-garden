import { expect, test, type Page } from '@playwright/test';

function collectConsoleErrors(page: Page): string[] {
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  return consoleErrors;
}

test('demo mounts a WebGL constellation without console errors', async ({ page }) => {
  const consoleErrors = collectConsoleErrors(page);

  await page.goto('/demo/');
  const canvas = page.locator('canvas');
  await expect(canvas).toBeVisible();

  await expect
    .poll(async () => page.locator('canvas').evaluate((element) => element.width > 0 && element.height > 0))
    .toBe(true);

  const hasWebGL = await canvas.evaluate((element) => {
    const canvasElement = element as HTMLCanvasElement;
    return Boolean(canvasElement.getContext('webgl2') ?? canvasElement.getContext('webgl'));
  });
  expect(hasWebGL).toBe(true);

  await page.keyboard.down('KeyW');
  await page.waitForTimeout(250);
  await page.keyboard.up('KeyW');

  expect(consoleErrors).toEqual([]);
});

test('package export can be consumed by a Vite browser app', async ({ page }) => {
  const consoleErrors = collectConsoleErrors(page);

  await page.goto('/tests/consumer.html');
  await expect
    .poll(async () => page.evaluate(() => (window as unknown as { consumerViewerMounted?: boolean }).consumerViewerMounted))
    .toBe(true);

  const canvas = page.locator('canvas');
  await expect(canvas).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test('clicking a sprite invokes onSelect with the picked image', async ({ page }) => {
  const consoleErrors = collectConsoleErrors(page);

  await page.goto('/tests/fixture.html');
  await page.evaluate(async () => {
    const { mount } = await import('/src/index.ts');
    const root = document.querySelector<HTMLElement>('#root');
    if (!root) throw new Error('Missing root');
    Object.assign(window, { selectedImageId: null });

    mount(
      root,
      {
        images: [
          {
            id: 'center',
            url: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E',
            position: [0, 0, 0],
            width: 256,
            height: 256,
          },
        ],
      },
      {
        camera: { position: [0, 0, 25] },
        controls: { enabled: false },
        sprites: { size: 12, lazyLoadDistance: 100 },
        onSelect: (image) => Object.assign(window, { selectedImageId: image.id }),
      },
    );
  });

  await page.mouse.click(320, 240);
  await expect
    .poll(async () => page.evaluate(() => (window as unknown as { selectedImageId: string | null }).selectedImageId))
    .toBe('center');

  expect(consoleErrors).toEqual([]);
});

test('near sprites shrink to stay below maximum viewport height', async ({ page }) => {
  const consoleErrors = collectConsoleErrors(page);

  await page.goto('/tests/fixture.html');
  const scales = await page.evaluate(async () => {
    const { mount } = await import('/src/index.ts');
    const root = document.querySelector<HTMLElement>('#root');
    if (!root) throw new Error('Missing root');

    const viewer = mount(
      root,
      {
        images: [
          { id: 'near', url: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E', position: [0, 0, 0] },
        ],
      },
      {
        camera: { fov: 90, position: [0, 0, 5] },
        controls: { enabled: false },
        sprites: { size: 10, maxViewportHeight: 0.2, lazyLoadDistance: 100 },
      },
    );

    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const privateViewer = viewer as unknown as {
      sceneHost: { camera: { position: { z: number } } };
      sprites: { records: Map<string, { mesh: { scale: { x: number } } }> };
    };
    const nearScale = privateViewer.sprites.records.get('near')?.mesh.scale.x;
    privateViewer.sceneHost.camera.position.z = 50;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const farScale = privateViewer.sprites.records.get('near')?.mesh.scale.x;
    viewer.destroy();
    return { nearScale, farScale };
  });

  expect(scales.nearScale).toBeLessThan(0.25);
  expect(scales.farScale).toBeCloseTo(1, 2);
  expect(consoleErrors).toEqual([]);
});

test('layout duplicate jitter deterministically splits same-coordinate stacks', async ({ page }) => {
  await page.goto('/tests/fixture.html');
  const result = await page.evaluate(async () => {
    const { computeLayout } = await import('/src/index.ts');
    const data = {
      images: [
        { id: 'a', url: 'x', position: [0, 0, 0] as const },
        { id: 'b', url: 'y', position: [0, 0, 0] as const },
        { id: 'far', url: 'z', position: [500, 0, 0] as const },
      ],
    };
    const options = {
      center: false,
      duplicateJitter: true,
      duplicateJitterDistance: 12,
      duplicateJitterMin: 50,
      duplicateJitterHalfLife: 50,
      duplicateJitterMax: 250,
    };
    const first = computeLayout(data, options).map((image) => image.position);
    const second = computeLayout(data, options).map((image) => image.position);
    const distance = (a: readonly number[], b: readonly number[]) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
    return {
      first,
      second,
      duplicateDistance: distance(first[0]!, first[1]!),
      farMoved: distance(first[2]!, [500, 0, 0]),
    };
  });

  expect(result.first).toEqual(result.second);
  expect(result.duplicateDistance).toBeGreaterThan(50);
  expect(result.farMoved).toBe(0);
});

test('public API supports mount, setData, setSelected, destroy, and validation', async ({ page }) => {
  const consoleErrors = collectConsoleErrors(page);

  await page.goto('/tests/fixture.html');
  await page.evaluate(async () => {
    const { mount } = await import('/src/index.ts');
    const root = document.querySelector<HTMLElement>('#root');
    if (!root) throw new Error('Missing root');

    const viewer = mount(
      root,
      {
        images: [
          { id: 'a', url: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E', position: [0, 0, 0] },
          { id: 'b', url: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E', position: [15, 0, 0] },
        ],
      },
      { controls: { enabled: false }, sprites: { lazyLoadDistance: 1_000 } },
    );

    if (root.querySelectorAll('canvas').length !== 1) throw new Error('Expected one canvas after mount');
    if (viewer.positions.length !== 2) throw new Error('Expected two positioned images');

    viewer.setSelected('a');
    viewer.setData({
      images: [
        { id: 'c', url: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg"/%3E', position: [5, 0, 0] },
      ],
    });
    if (viewer.positions.length !== 1 || viewer.positions[0]?.id !== 'c') {
      throw new Error('setData did not replace positioned images');
    }

    let duplicateRejected = false;
    try {
      viewer.setData({
        images: [
          { id: 'dup', url: 'x', position: [0, 0, 0] },
          { id: 'dup', url: 'y', position: [1, 0, 0] },
        ],
      });
    } catch {
      duplicateRejected = true;
    }
    if (!duplicateRejected) throw new Error('Duplicate ids should be rejected');

    viewer.destroy();
    if (root.querySelectorAll('canvas').length !== 0) throw new Error('Expected canvas to be removed after destroy');
  });

  expect(consoleErrors).toEqual([]);
});

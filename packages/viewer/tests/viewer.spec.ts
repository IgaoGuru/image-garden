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

test('data-source adapters preserve high-res thumbnail URLs', async ({ page }) => {
  await page.goto('/tests/fixture.html');
  const result = await page.evaluate(async () => {
    const { createStaticDataSource, imageToRuntimeAsset, runtimeAssetsToData } = await import('/src/index.ts');
    const asset = {
      id: 'one',
      thumbnailUrl: '/thumb.jpg',
      highResThumbnailUrl: '/highres.jpg',
      fullUrl: '/full.jpg',
      position: [1, 2, 3] as [number, number, number],
    };
    const data = runtimeAssetsToData([asset]);
    const runtimeAsset = imageToRuntimeAsset(data.images[0]!);
    const source = createStaticDataSource(data);
    const initialAssets = await source.getInitialAssets();
    return {
      dataHighResUrl: data.images[0]?.highResThumbnailUrl,
      runtimeHighResUrl: runtimeAsset.highResThumbnailUrl,
      staticHighResUrl: initialAssets[0]?.highResThumbnailUrl,
    };
  });

  expect(result).toEqual({
    dataHighResUrl: '/highres.jpg',
    runtimeHighResUrl: '/highres.jpg',
    staticHighResUrl: '/highres.jpg',
  });
});

test('fetch data source supports explicit backend endpoint adapters', async ({ page }) => {
  await page.goto('/tests/fixture.html');
  const result = await page.evaluate(async () => {
    const { createFetchDataSource } = await import('/src/index.ts');
    const requests: string[] = [];
    const source = createFetchDataSource({
      baseUrl: 'https://example.test/root/',
      initialLimit: 2,
      initialOffset: 4,
      endpoints: {
        status: 'status.json',
        assets: 'assets.json',
        nearAssets: 'near-assets.json',
        asset: (id: string) => `asset/${encodeURIComponent(id)}.json`,
      },
      fetch: async (input: RequestInfo | URL) => {
        const url = String(input);
        requests.push(url);
        if (url.includes('status.json')) {
          return new Response(JSON.stringify({ state: 'ready', totalAssets: 1 }), { status: 200 });
        }
        if (url.includes('near-assets.json')) {
          return new Response(JSON.stringify({ assets: [] }), { status: 200 });
        }
        if (url.includes('asset/')) {
          return new Response(JSON.stringify({ id: 'one', thumbnailUrl: '/thumb.jpg', highResThumbnailUrl: '/highres.jpg', position: [1, 2, 3] }), { status: 200 });
        }
        return new Response(JSON.stringify({ assets: [{ id: 'one', thumbnailUrl: '/thumb.jpg', highResThumbnailUrl: '/highres.jpg', position: [1, 2, 3] }] }), { status: 200 });
      },
    });
    const status = await source.getStatus();
    const assets = await source.getInitialAssets();
    const near = await source.getNearbyAssets?.({ x: 1, y: 2, z: 3, radius: 4, limit: 5 });
    const asset = await source.getAsset?.('one/two');
    return { requests, status, assets, near, asset };
  });

  expect(result.status.state).toBe('ready');
  expect(result.assets).toHaveLength(1);
  expect(result.assets[0]?.highResThumbnailUrl).toBe('/highres.jpg');
  expect(result.near).toEqual([]);
  expect(result.asset?.id).toBe('one');
  expect(result.asset?.highResThumbnailUrl).toBe('/highres.jpg');
  expect(result.requests).toEqual([
    'https://example.test/root/status.json',
    'https://example.test/root/assets.json?limit=2&offset=4',
    'https://example.test/root/near-assets.json?x=1&y=2&z=3&radius=4&limit=5',
    'https://example.test/root/asset/one%2Ftwo.json',
  ]);
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

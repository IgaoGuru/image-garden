import { mount, type ConstellationViewer, type RuntimeAsset } from '@constellation/viewer';
import './style.css';

interface ApiStatus {
  state?: string;
  totalAssets?: number;
  jobPhase?: string;
  jobCompleted?: number;
  jobTotal?: number;
  jobMessage?: string;
  lastImportPath?: string;
  [key: string]: unknown;
}

interface DesktopBridge {
  openImportFolder?: () => Promise<ImportResult | undefined>;
  openImportStudio?: () => Promise<ImportResult | undefined>;
}

interface PlayviewDebugSnapshot {
  status: ApiStatus | null;
  viewer: ReturnType<ConstellationViewer['getDebugStats']> | null;
  resources: {
    assetPageRequests: number;
    atlasPageRequests: number;
    textureArrayPageRequests: number;
    thumbnailRequests: number;
    fileRequests: number;
  };
  pointerLock: boolean;
}

interface ImportResult {
  ok?: boolean;
  canceled?: boolean;
  error?: string;
}

interface AssetPage {
  assets?: RuntimeAsset[];
  total?: number;
  limit?: number;
  offset?: number;
}

declare global {
  interface Window {
    constellationDesktop?: DesktopBridge;
    imageGardenDebug?: () => PlayviewDebugSnapshot;
  }
}

const status = mustQuery<HTMLElement>('#status');
const helpText = mustQuery<HTMLElement>('#help-text');
const root = mustQuery<HTMLElement>('#viewer');
const onboarding = mustQuery<HTMLElement>('#onboarding');
const progress = mustQuery<HTMLElement>('#progress');
const progressPhase = mustQuery<HTMLElement>('#progress-phase');
const progressCount = mustQuery<HTMLElement>('#progress-count');
const progressFill = mustQuery<HTMLElement>('#progress-fill');
const starfield = mustQuery<HTMLElement>('#starfield');
const progressLog = mustQuery<HTMLElement>('#progress-log');
const menu = mustQuery<HTMLElement>('#menu');
const debug = mustQuery<HTMLElement>('#debug');
const windVolumeInput = mustQuery<HTMLInputElement>('#wind-volume');
const windVolumeValue = mustQuery<HTMLOutputElement>('#wind-volume-value');
const windStatus = mustQuery<HTMLElement>('#wind-status');
const desktop = window.constellationDesktop;

let viewerInstance: ConstellationViewer | null = null;
let latestStatus: ApiStatus | null = null;
let assets: RuntimeAsset[] = [];
let starCount = 0;
let targetStarCount = 0;
let lastProgressLogKey = '';
let visibleProgress = 0;
let helpTimer = 0;
let idleTimer = 0;
let debugTimer = 0;
let spinnerFrame = 0;
let wasPointerLocked = false;
let tutorialActive = false;
let tutorialTransitioning = false;
let tutorialIndex = 0;
let tutorialStepStartedAt = 0;

const verticalTutorialKeys = new Set<string>();
const tutorialSteps = [
  { id: 'move', text: 'use <kbd>W</kbd>/<kbd>A</kbd>/<kbd>S</kbd>/<kbd>D</kbd> to move around' },
  { id: 'look', text: 'move your <span class="mouse-icon">🖱</span> mouse to move your view' },
  { id: 'vertical', text: 'use <kbd>space</kbd> and <kbd>C</kbd> to go up and down' },
  { id: 'slow', text: 'hold <kbd>shift</kbd> to move faster' },
  { id: 'menu', text: 'press <kbd>esc</kbd> to see the menu' },
] as const;
const spinnerFrames = ['⠁', '⠂', '⠄', '⡀', '⢀', '⠠'];
const progressLogEntries: string[] = [];
const windVolumeStorageKey = 'constellation.windVolume';
const windAmbienceUrl = '/audio/wind-ambience.mp3';
const minTutorialStepMs = 5_500;
const assetPageSize = 5_000;

window.imageGardenDebug = readDebugSnapshot;

setupWindAmbience();

window.setInterval(() => {
  if (!progress.classList.contains('visible') || progressLogEntries.length === 0) return;
  spinnerFrame = (spinnerFrame + 1) % spinnerFrames.length;
  renderProgressLog();
}, 140);

void boot();

async function boot(): Promise<void> {
  try {
    const [loadedAssets, initialStatus] = await Promise.all([
      fetchAllAssets(),
      fetchJson<ApiStatus>('/api/status').catch(() => null),
    ]);
    assets = loadedAssets;
    latestStatus = initialStatus;
    if (assets.length > 0) {
      mountViewer(assets);
      startTutorial();
    } else {
      status.textContent = '';
      showOnboarding();
    }
    installGlobalHandlers();
    scheduleIdleHelp();
  } catch (error) {
    status.textContent = `startup failed: ${errorMessage(error)}`;
    showOnboarding();
  }
}

async function fetchAllAssets(): Promise<RuntimeAsset[]> {
  const loaded: RuntimeAsset[] = [];
  let offset = 0;
  let total: number | null = null;

  while (total === null || offset < total) {
    const payload = await fetchJson<AssetPage>(`/api/assets?limit=${assetPageSize}&offset=${offset}`);
    const page = payload.assets ?? [];
    loaded.push(...page);
    offset += page.length;
    if (typeof payload.total === 'number') total = payload.total;
    if (page.length === 0 || page.length < assetPageSize) break;
    if (total !== null && total > assetPageSize) status.textContent = `loading catalog ${loaded.length}/${total}`;
    await delay(0);
  }

  return loaded;
}

function mountViewer(nextAssets: RuntimeAsset[]): void {
  viewerInstance?.destroy();
  viewerInstance = mount(
    root,
    { images: nextAssets.map((asset) => ({ ...asset, url: asset.fullUrl ?? asset.thumbnailUrl })) },
    {
      backgroundColor: 0x000000,
      sprites: {
        renderMode: 'auto',
        textureArray: true,
        textureArrayPageConcurrency: 4,
        textureArrayMaxPages: 40,
        atlas: true,
        atlasPageConcurrency: 6,
        atlasMaxPages: 24,
        lazyLoadDistance: 1_000,
        textureUnloadDistance: 1_200,
        maxTexturedCards: 9_000,
        maxLoadedTextures: 9_000,
      },
      layout: { center: false },
    },
  );
  status.textContent = `${nextAssets.length} images`;
  requestAnimationFrame(() => root.classList.add('visible'));
}

function installGlobalHandlers(): void {
  for (const type of ['pointerdown', 'wheel', 'keydown'] as const) {
    window.addEventListener(type, noteActivity, { passive: true });
  }
  window.addEventListener('pointermove', (event) => {
    handleTutorialPointerMove(event);
    noteActivity();
  }, { passive: true });

  document.addEventListener('keydown', (event) => {
    handleTutorialKey(event);
    if (event.key !== 'Escape') return;
    if (onboarding.classList.contains('visible') || progress.classList.contains('visible')) return;
    event.preventDefault();
    toggleMenu();
    if (!tutorialActive) showHelp('wasd move · shift fast · spacebar up · c down · esc go back', 6000);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key.toLowerCase() !== 'l' || !event.shiftKey) return;
    if (!isPlayviewVisible()) return;
    event.preventDefault();
    void toggleLiveDebug();
  });

  document.addEventListener('pointerlockchange', () => {
    const canvas = root.querySelector('canvas');
    const isPointerLocked = document.pointerLockElement === canvas;
    if (wasPointerLocked && !isPointerLocked && isPlayviewVisible() && !menu.classList.contains('visible')) {
      completeTutorialStep('menu');
      showMenu();
      if (!tutorialActive) showHelp('wasd move · shift fast · spacebar up · c down · esc go back', 6000);
    }
    wasPointerLocked = isPointerLocked;
  });

  menu.addEventListener('click', (event) => {
    const button = (event.target as Element | null)?.closest<HTMLButtonElement>('button[data-menu]');
    if (!button) return;
    void handleMenuAction(button.dataset.menu ?? '');
  });
}

async function handleMenuAction(action: string): Promise<void> {
  if (action === 'close') hideMenu();
  if (action === 'reset-camera') { viewerInstance?.resetCamera(); hideMenu(); }
  if (action === 'fit-constellation') { viewerInstance?.fitToContent(); hideMenu(); startTutorial(); }
  if (action === 'reimport') await reimportLastFolder();
  if (action === 'open-data') await postJson('/api/system/open-data-dir', {});
  if (action === 'clear-data') await clearData();
  if (action === 'debug') await showDebug();
}

function setupWindAmbience(): void {
  const audio = new Audio(windAmbienceUrl);
  audio.loop = true;
  audio.preload = 'auto';

  let started = false;
  let playAttemptInFlight = false;

  const savedVolume = readStoredPercent(windVolumeStorageKey, 70);
  windVolumeInput.value = String(savedVolume);
  applyVolume(savedVolume);

  const start = (): void => {
    if (started || playAttemptInFlight || audio.volume <= 0) return;
    playAttemptInFlight = true;
    void audio
      .play()
      .then(() => {
        started = true;
        windStatus.textContent = 'wind ambience playing';
        removeGestureListeners();
      })
      .catch(() => {
        windStatus.textContent = 'click again or move the slider to start ambience';
      })
      .finally(() => {
        playAttemptInFlight = false;
      });
  };

  const onGesture = (): void => start();

  function addGestureListeners(): void {
    window.addEventListener('pointerdown', onGesture, { passive: true });
    window.addEventListener('keydown', onGesture);
  }

  function removeGestureListeners(): void {
    window.removeEventListener('pointerdown', onGesture);
    window.removeEventListener('keydown', onGesture);
  }

  addGestureListeners();

  windVolumeInput.addEventListener('input', () => {
    const volume = Number(windVolumeInput.value);
    writeStoredPercent(windVolumeStorageKey, volume);
    applyVolume(volume);
    if (volume > 0) {
      start();
    } else {
      audio.pause();
      started = false;
      windStatus.textContent = 'wind ambience off';
      addGestureListeners();
    }
  });

  function applyVolume(value: number): void {
    const clamped = clampPercent(value);
    audio.volume = clamped / 100;
    audio.muted = clamped === 0;
    windVolumeInput.style.setProperty('--volume', `${clamped}%`);
    windVolumeValue.value = `${clamped}%`;
    windVolumeValue.textContent = `${clamped}%`;
    if (!started) {
      windStatus.textContent = clamped > 0 ? 'click the constellation to start ambience' : 'wind ambience off';
    }
  }
}

function readStoredPercent(key: string, fallback: number): number {
  try {
    const rawValue = window.localStorage.getItem(key);
    if (rawValue === null) return fallback;
    return clampPercent(Number(rawValue));
  } catch {
    return fallback;
  }
}

function writeStoredPercent(key: string, value: number): void {
  try {
    window.localStorage.setItem(key, String(clampPercent(value)));
  } catch {
    // Ignore storage failures; the slider still controls this session.
  }
}

function clampPercent(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.min(100, Math.round(value))) : 0;
}

function showOnboarding(): void {
  onboarding.innerHTML = onboardingMarkup();
  onboarding.classList.add('visible');
  requestAnimationFrame(() => onboarding.classList.add('fade-in'));
  const dropZone = onboarding.querySelector<HTMLElement>('.drop-zone');
  dropZone?.addEventListener('click', () => void chooseFolder());
  dropZone?.addEventListener('dragenter', (event) => { event.preventDefault(); dropZone.classList.add('dragging'); });
  dropZone?.addEventListener('dragover', (event) => { event.preventDefault(); dropZone.classList.add('dragging'); });
  dropZone?.addEventListener('dragleave', () => dropZone.classList.remove('dragging'));
  dropZone?.addEventListener('drop', (event) => void handleDrop(event));
  onboarding.querySelector('[data-action="choose-studio"]')?.addEventListener('click', () => void chooseStudioDataset());
  onboarding.querySelector('[data-manual-form="folder"]')?.addEventListener('submit', (event) => void submitManualFolder(event));
  onboarding.querySelector('[data-manual-form="studio"]')?.addEventListener('submit', (event) => void submitManualStudio(event));
}

function onboardingMarkup(): string {
  return `<div class="onboarding-panel">
    <h1 class="title">build your constellation of images</h1>
    <button class="drop-zone" type="button" data-action="choose-folder">drop your photos directory here</button>
    <form class="manual-form" data-manual-form="folder">
      <input name="path" placeholder="/absolute/path/to/photos" autocomplete="off">
      <button type="submit">import</button>
    </form>
    <button class="dataset-button" type="button" data-action="choose-studio">already have a image-embedding dataset?</button>
    <form class="manual-form" data-manual-form="studio">
      <input name="path" placeholder="/path/to/constellation.json" autocomplete="off">
      <button type="submit">open</button>
    </form>
  </div>`;
}

async function handleDrop(event: DragEvent): Promise<void> {
  event.preventDefault();
  (event.currentTarget as HTMLElement | null)?.classList.remove('dragging');
  const path = [...(event.dataTransfer?.files ?? [])]
    .map((file) => (file as File & { path?: string }).path)
    .find((value): value is string => typeof value === 'string' && value.length > 0);
  if (path) {
    await startFolderImport(path);
    return;
  }
  status.textContent = 'directory path unavailable; opening picker';
  await chooseFolder();
}

async function chooseFolder(): Promise<void> {
  const openImportFolder = desktop?.openImportFolder;
  if (openImportFolder) {
    await runDesktopImport(openImportFolder, 'importing directory');
    return;
  }
  const path = await chooseBackendPath('/api/dialog/folder', 'choose a directory');
  if (path) {
    await startFolderImport(path);
    return;
  }
  showManualForm('folder');
}

async function chooseStudioDataset(): Promise<void> {
  const openImportStudio = desktop?.openImportStudio;
  if (openImportStudio) {
    await runDesktopImport(openImportStudio, 'importing dataset');
    return;
  }
  const path = await chooseBackendPath('/api/dialog/studio', 'choose a dataset');
  if (path) {
    await submitImportPath('/api/import/studio', path, 'opening dataset');
    return;
  }
  showManualForm('studio');
}

function showManualForm(name: 'folder' | 'studio'): void {
  const form = onboarding.querySelector<HTMLElement>(`[data-manual-form="${name}"]`);
  form?.classList.add('visible');
  form?.querySelector<HTMLInputElement>('input')?.focus();
}

async function chooseBackendPath(endpoint: string, message: string): Promise<string | null> {
  status.textContent = message;
  try {
    const response = await postJson<{ ok?: boolean; path?: unknown }>(endpoint, {});
    if (response.ok && typeof response.path === 'string') return response.path;
    status.textContent = 'no path selected';
    return null;
  } catch (error) {
    status.textContent = `picker failed: ${errorMessage(error)}`;
    return null;
  }
}

async function runDesktopImport(importer: () => Promise<ImportResult | undefined>, message: string): Promise<void> {
  showProgress({ jobPhase: message, jobCompleted: 0, jobTotal: 0 });
  try {
    const result = await importer();
    if (result?.ok) window.location.reload();
    else if (!result?.canceled) status.textContent = result?.error ?? 'import canceled';
  } catch (error) {
    status.textContent = `import failed: ${errorMessage(error)}`;
  } finally {
    hideProgress();
  }
}

async function submitManualFolder(event: Event): Promise<void> {
  event.preventDefault();
  const path = new FormData(event.currentTarget as HTMLFormElement).get('path');
  if (typeof path !== 'string' || !path.trim()) return;
  await startFolderImport(path);
}

async function submitManualStudio(event: Event): Promise<void> {
  event.preventDefault();
  const path = new FormData(event.currentTarget as HTMLFormElement).get('path');
  if (typeof path !== 'string' || !path.trim()) return;
  await submitImportPath('/api/import/studio', path, 'opening dataset');
}

async function startFolderImport(path: string): Promise<void> {
  showProgress({ jobPhase: 'queued', jobCompleted: 0, jobTotal: 0 });
  const result = await postJson<{ ok?: boolean; error?: string }>('/api/import/folder', { path, background: true }).catch((error: unknown) => ({ ok: false, error: errorMessage(error) }));
  if (!result.ok) {
    hideProgress();
    status.textContent = `error — ${result.error ?? 'import failed'}`;
    return;
  }
  onboarding.classList.remove('fade-in', 'visible');
  await pollImportProgress();
}

async function submitImportPath(endpoint: string, path: string, label: string): Promise<void> {
  status.textContent = label;
  const result = await postJson<{ ok?: boolean; error?: string }>(endpoint, { path }).catch((error: unknown) => ({ ok: false, error: errorMessage(error) }));
  if (!result.ok) {
    status.textContent = `error — ${result.error ?? 'import failed'}`;
    return;
  }
  window.location.reload();
}

async function pollImportProgress(): Promise<void> {
  for (;;) {
    await delay(500);
    const current = await fetchJson<ApiStatus>('/api/status');
    latestStatus = current;
    showProgress(current);
    if (current.state === 'error' || current.jobPhase === 'error') {
      hideProgress();
      status.textContent = `error — ${current.jobMessage ?? 'import failed'}`;
      return;
    }
    if ((current.jobPhase === 'ready' || current.state === 'idle') && (current.totalAssets ?? 0) > 0) {
      window.location.reload();
      return;
    }
  }
}

function showProgress(current: ApiStatus): void {
  const serverCompleted = current.jobCompleted ?? 0;
  const total = current.jobTotal ?? 0;
  const phase = current.jobPhase ?? current.state ?? 'working';
  visibleProgress = nextVisibleProgress(visibleProgress, serverCompleted, total, phase);
  const displayCompleted = total > 0 ? Math.min(total, Math.floor(visibleProgress)) : serverCompleted;
  const percent = total > 0 ? Math.min(100, Math.round((visibleProgress / total) * 100)) : 3;
  progress.classList.add('visible');
  progressPhase.textContent = phase;
  progressCount.textContent = total > 0 ? `${displayCompleted} / ${total}` : '';
  progressFill.style.width = `${percent}%`;
  status.textContent = total > 0 ? `${phase} ${displayCompleted}/${total}` : phase;
  targetStarCount = total > 0 ? Math.min(160, Math.max(8, Math.ceil((visibleProgress / Math.max(total, 1)) * 160))) : 12;
  drainStarQueue();
  maybeLogProgress(phase, displayCompleted, total, current.jobMessage ?? '');
}

function nextVisibleProgress(current: number, serverCompleted: number, total: number, phase: string): number {
  if (total <= 0) return Math.max(current, serverCompleted);
  if (serverCompleted >= total || phase === 'ready' || phase === 'layout') return serverCompleted;
  const floor = Math.max(current, serverCompleted);
  const trickleCap = Math.min(total - 1, serverCompleted + Math.max(1, total * 0.08));
  return Math.min(trickleCap, floor + Math.max(0.18, total * 0.004));
}

function maybeLogProgress(phase: string, completed: number, total: number, message: string): void {
  const key = `${phase}:${completed}:${total}:${message}`;
  if (key === lastProgressLogKey) return;
  lastProgressLogKey = key;
  const count = total > 0 ? ` ${completed}/${total}` : '';
  const suffix = message ? ` — ${message}` : '';
  progressLogEntries.unshift(`${phase}${count}${suffix}`);
  progressLogEntries.length = Math.min(progressLogEntries.length, 8);
  renderProgressLog();
}

function renderProgressLog(): void {
  progressLog.replaceChildren(...progressLogEntries.slice(0, 4).map((entry, index) => {
    const line = document.createElement('div');
    line.className = 'progress-log-line';
    line.textContent = index === 0 ? `${spinnerFrames[spinnerFrame] ?? ''} ${entry}` : `  ${entry}`;
    return line;
  }));
}

function hideProgress(): void {
  progress.classList.remove('visible');
  targetStarCount = 0;
}

function drainStarQueue(): void {
  if (!progress.classList.contains('visible')) return;
  if (starCount < targetStarCount) addStar();
  if (starCount < targetStarCount) window.setTimeout(drainStarQueue, 38);
}

function addStar(): void {
  starCount += 1;
  const star = document.createElement('span');
  star.className = 'star';
  const angle = (starCount * 137.508) * Math.PI / 180;
  const radius = Math.sqrt(starCount / 160) * 44;
  const jitterX = (Math.random() - 0.5) * 10;
  const jitterY = (Math.random() - 0.5) * 10;
  star.style.left = `${50 + Math.cos(angle) * radius + jitterX}%`;
  star.style.top = `${50 + Math.sin(angle) * radius + jitterY}%`;
  starfield.append(star);
}

function isPlayviewVisible(): boolean {
  return assets.length > 0 && !onboarding.classList.contains('visible') && !progress.classList.contains('visible');
}

function startTutorial(): void {
  if (!isPlayviewVisible()) return;
  tutorialActive = true;
  tutorialTransitioning = false;
  tutorialIndex = 0;
  tutorialStepStartedAt = 0;
  verticalTutorialKeys.clear();
  showTutorialStep();
}

function showTutorialStep(): void {
  if (!tutorialActive || !isPlayviewVisible()) return;
  const step = tutorialSteps[tutorialIndex];
  if (!step) {
    tutorialActive = false;
    hideHelp();
    return;
  }
  helpText.innerHTML = step.text;
  helpText.classList.add('visible');
  tutorialStepStartedAt = performance.now();
  window.clearTimeout(helpTimer);
}

function completeTutorialStep(stepId: string): void {
  const step = tutorialSteps[tutorialIndex];
  if (!tutorialActive || tutorialTransitioning || step?.id !== stepId) return;
  tutorialTransitioning = true;
  const visibleForMs = performance.now() - tutorialStepStartedAt;
  const remainingVisibleMs = Math.max(0, minTutorialStepMs - visibleForMs);
  window.clearTimeout(helpTimer);
  helpTimer = window.setTimeout(() => {
    helpText.classList.remove('visible');
    helpTimer = window.setTimeout(() => {
      tutorialIndex += 1;
      tutorialTransitioning = false;
      if (tutorialIndex >= tutorialSteps.length) {
        tutorialActive = false;
        hideHelp();
        return;
      }
      showTutorialStep();
    }, 1500);
  }, remainingVisibleMs);
}

function handleTutorialKey(event: KeyboardEvent): void {
  if (!tutorialActive || tutorialTransitioning) return;
  const step = tutorialSteps[tutorialIndex];
  if (step?.id === 'move' && ['KeyW', 'KeyA', 'KeyS', 'KeyD'].includes(event.code)) completeTutorialStep('move');
  if (step?.id === 'vertical' && ['Space', 'KeyC'].includes(event.code)) {
    verticalTutorialKeys.add(event.code);
    if (verticalTutorialKeys.has('Space') && verticalTutorialKeys.has('KeyC')) completeTutorialStep('vertical');
  }
  if (step?.id === 'slow' && ['ShiftLeft', 'ShiftRight'].includes(event.code)) completeTutorialStep('slow');
  if (step?.id === 'menu' && event.key === 'Escape') completeTutorialStep('menu');
}

function handleTutorialPointerMove(event: PointerEvent): void {
  const moved = Math.abs(event.movementX) + Math.abs(event.movementY) > 0;
  if (tutorialActive && !tutorialTransitioning && tutorialSteps[tutorialIndex]?.id === 'look' && moved) completeTutorialStep('look');
}

function showHelp(message: string, duration = 4200): void {
  if (!isPlayviewVisible() || tutorialActive) return;
  helpText.textContent = message;
  helpText.classList.add('visible');
  window.clearTimeout(helpTimer);
  helpTimer = window.setTimeout(() => helpText.classList.remove('visible'), duration);
}

function hideHelp(): void {
  if (menu.classList.contains('visible') || tutorialActive) return;
  helpText.classList.remove('visible');
  window.clearTimeout(helpTimer);
}

function scheduleIdleHelp(): void {
  window.clearTimeout(idleTimer);
  idleTimer = window.setTimeout(() => showHelp('wasd move · shift fast · spacebar up · c down · esc go back', 6000), 5000);
}

function noteActivity(): void {
  if (!tutorialActive) hideHelp();
  scheduleIdleHelp();
}

function toggleMenu(): void {
  if (menu.classList.contains('visible')) hideMenu();
  else showMenu();
}

function showMenu(): void {
  menu.classList.add('visible');
  menu.setAttribute('aria-hidden', 'false');
  menu.querySelector<HTMLButtonElement>('button')?.focus();
}

function hideMenu(): void {
  menu.classList.remove('visible');
  menu.setAttribute('aria-hidden', 'true');
  stopLiveDebug();
  root.querySelector<HTMLCanvasElement>('canvas')?.focus();
}

async function clearData(): Promise<void> {
  if (!window.confirm('clear all local constellation data?')) return;
  await postJson('/api/data/clear', {});
  window.location.reload();
}

async function reimportLastFolder(): Promise<void> {
  const current = latestStatus ?? await fetchJson<ApiStatus>('/api/status');
  const path = current.lastImportPath;
  if (!path) {
    status.textContent = 'no last folder';
    return;
  }
  hideMenu();
  await startFolderImport(path);
}

async function showDebug(): Promise<void> {
  await toggleLiveDebug();
}

async function toggleLiveDebug(): Promise<void> {
  if (debug.classList.contains('visible')) {
    stopLiveDebug();
    return;
  }
  if (document.pointerLockElement) document.exitPointerLock();
  if (!menu.classList.contains('visible')) showMenu();
  debug.classList.add('visible');
  await refreshLiveDebug();
  window.clearInterval(debugTimer);
  debugTimer = window.setInterval(() => {
    void refreshLiveDebug();
  }, 500);
}

function stopLiveDebug(): void {
  window.clearInterval(debugTimer);
  debugTimer = 0;
  debug.classList.remove('visible');
}

async function refreshLiveDebug(): Promise<void> {
  const current = await fetchJson<ApiStatus>('/api/status').catch(() => null);
  if (current) latestStatus = current;
  debug.textContent = JSON.stringify(
    {
      ...readDebugSnapshot(),
      status: current,
    },
    null,
    2,
  );
}

function readDebugSnapshot(): PlayviewDebugSnapshot {
  const resourceEntries = performance.getEntriesByType('resource');
  const thumbnailRequests = resourceEntries.filter((entry) => entry.name.includes('/api/thumbnails/')).length;
  const fileRequests = resourceEntries.filter((entry) => entry.name.includes('/api/files/')).length;
  const assetPageRequests = resourceEntries.filter((entry) => entry.name.includes('/api/assets')).length;
  const atlasPageRequests = resourceEntries.filter((entry) => entry.name.includes('/api/atlas/pages')).length;
  const textureArrayPageRequests = resourceEntries.filter((entry) => entry.name.includes('/api/texture-array/pages')).length;
  return {
    status: latestStatus,
    viewer: viewerInstance?.getDebugStats() ?? null,
    resources: {
      assetPageRequests,
      atlasPageRequests,
      textureArrayPageRequests,
      thumbnailRequests,
      fileRequests,
    },
    pointerLock: document.pointerLockElement === root.querySelector('canvas'),
  };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

async function postJson<T = { ok?: boolean }>(url: string, payload: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const result = await response.json() as T & { ok?: boolean; error?: string };
  if (!response.ok || result.ok === false) throw new Error(result.error ?? response.statusText);
  return result;
}

function mustQuery<T extends Element>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`missing ${selector}`);
  return element;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

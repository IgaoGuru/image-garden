"""Local backend API for Constellation desktop/runtime clients."""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import platform
import shutil
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Generator, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import ClassVar, cast
from urllib.parse import parse_qs, unquote, urlsplit

from constellation_studio.embed import DEFAULT_MODEL, DEFAULT_PRETRAINED
from constellation_studio.embedding_providers import (
    EmbeddingProvider,
    create_embedding_provider,
)
from constellation_studio.index_store import IndexStore
from constellation_studio.indexing import (
    ImportResult,
    default_indexing_paths,
    import_folder,
    import_studio_dataset,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000

BACKEND_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Constellation</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; width: 100%; min-height: 100%; background: #000; color: #f6f1e8; overflow: hidden; }
    body { font: 14px/1.4 ui-monospace, "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
    button, input { font: inherit; }
    button { color: #f6f1e8; background: transparent; border: 1px solid rgba(246,241,232,.25); border-radius: 0; padding: 10px 14px; cursor: pointer; transition: border-color .16s ease, background .16s ease, opacity .16s ease; }
    button:hover:not(:disabled), button:focus-visible { border-color: rgba(246,241,232,.72); background: rgba(246,241,232,.055); outline: none; }
    button:disabled { cursor: not-allowed; opacity: .42; }
    input { width: 100%; color: #f6f1e8; background: #050505; border: 1px solid rgba(246,241,232,.24); padding: 11px 12px; outline: none; }
    input:focus { border-color: rgba(246,241,232,.72); }
    #viewer { position: fixed; inset: 0; width: 100vw; height: 100vh; background: #000; opacity: 0; transition: opacity 1400ms ease; }
    #viewer.visible { opacity: 1; }
    #status { position: fixed; left: 18px; bottom: 16px; z-index: 5; max-width: calc(100vw - 36px); color: rgba(246,241,232,.54); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; pointer-events: none; }
    #help-text { position: fixed; left: 50%; bottom: 52px; z-index: 21; transform: translateX(-50%); color: rgba(246,241,232,.58); font-size: 15px; letter-spacing: .01em; white-space: nowrap; pointer-events: none; opacity: 0; transition: opacity 260ms ease; }
    #help-text.visible { opacity: 1; }
    #help-text kbd { color: rgba(246,241,232,.86); font: inherit; }
    .mouse-icon { color: rgba(246,241,232,.82); }
    #onboarding { position: fixed; inset: 0; z-index: 10; display: none; align-items: center; justify-content: center; padding: 28px; background: #000; opacity: 0; transition: opacity 1200ms ease; }
    #onboarding.visible { display: flex; }
    #onboarding.fade-in { opacity: 1; }
    .onboarding-panel { width: min(520px, 100%); display: grid; gap: 24px; justify-items: center; animation: hello 1200ms ease both; }
    @keyframes hello { from { transform: translateY(8px); filter: blur(4px); } to { transform: translateY(0); filter: blur(0); } }
    .title { margin: 0; color: rgba(246,241,232,.88); font-size: clamp(15px, 2.4vw, 19px); font-weight: 400; letter-spacing: .01em; text-align: center; }
    .drop-zone { width: min(420px, 72vw); aspect-ratio: 1; display: grid; place-items: center; padding: 24px; color: rgba(246,241,232,.68); border: 1px solid rgba(246,241,232,.24); background: radial-gradient(circle at 50% 50%, rgba(246,241,232,.045), rgba(246,241,232,.015) 44%, transparent 70%); text-align: center; }
    .drop-zone:hover, .drop-zone.dragging { border-color: rgba(246,241,232,.82); background: rgba(246,241,232,.04); }
    .dataset-button { border: 0; color: rgba(246,241,232,.52); padding: 4px; font-size: 12px; }
    .dataset-button:hover, .dataset-button:focus-visible { color: rgba(246,241,232,.9); background: transparent; }
    .manual-form { display: none; width: min(420px, 72vw); grid-template-columns: 1fr auto; gap: 8px; }
    .manual-form.visible { display: grid; }
    .manual-form[data-manual-form="studio"] { margin-top: -12px; }
    #progress { position: fixed; inset: 0; z-index: 12; display: none; align-items: center; justify-content: center; background: rgba(0,0,0,.94); opacity: 0; transition: opacity 240ms ease; }
    #progress.visible { display: flex; opacity: 1; }
    .progress-panel { width: min(520px, calc(100vw - 48px)); display: grid; gap: 18px; }
    .progress-line { display: flex; justify-content: space-between; gap: 16px; color: rgba(246,241,232,.72); font-size: 12px; }
    .bar { height: 1px; background: rgba(246,241,232,.18); overflow: hidden; }
    .bar-fill { width: 0%; height: 100%; background: rgba(246,241,232,.86); transition: width 260ms ease; }
    .starfield { position: relative; height: 170px; border: 1px solid rgba(246,241,232,.12); background: radial-gradient(circle at 50% 50%, rgba(246,241,232,.035), transparent 72%); overflow: hidden; }
    .star { position: absolute; width: 2px; height: 2px; border-radius: 999px; background: rgba(246,241,232,.84); opacity: .2; transform: scale(.4); animation: starBirth 720ms ease forwards; }
    @keyframes starBirth { to { opacity: .9; transform: scale(1); } }
    .progress-log { display: grid; gap: 3px; min-height: 66px; overflow: hidden; color: rgba(246,241,232,.36); font-size: 11px; }
    .progress-log-line { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: opacity .3s ease; }
    .progress-log-line:nth-child(1) { color: rgba(246,241,232,.78); opacity: 1; }
    .progress-log-line:nth-child(2) { opacity: .62; }
    .progress-log-line:nth-child(3) { opacity: .38; }
    .progress-log-line:nth-child(4) { opacity: .18; }
    #menu { position: fixed; inset: 0; z-index: 20; display: none; align-items: center; justify-content: center; background: rgba(0,0,0,.74); opacity: 0; transition: opacity 140ms ease; }
    #menu.visible { display: flex; opacity: 1; }
    .menu-panel { width: min(360px, calc(100vw - 42px)); display: grid; gap: 8px; padding: 18px; border: 1px solid rgba(246,241,232,.18); background: rgba(0,0,0,.92); }
    .menu-panel button { width: 100%; text-align: left; border-color: transparent; color: rgba(246,241,232,.72); }
    .menu-panel button:hover, .menu-panel button:focus-visible { color: #f6f1e8; border-color: rgba(246,241,232,.26); }
    .debug { display: none; max-height: 220px; overflow: auto; margin: 8px 0 0; padding: 10px; border: 1px solid rgba(246,241,232,.12); color: rgba(246,241,232,.62); font-size: 11px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .debug.visible { display: block; }
    .fallback { padding: 20px; overflow: auto; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 14px; }
    .card { background: #080808; border: 1px solid #333; overflow: hidden; }
    .card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: #000; }
    .card div { padding: 8px; font-size: 12px; color: #bbb; overflow-wrap: anywhere; }
    @media (max-width: 620px) { .manual-form { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main id="viewer"></main>
  <div id="status" aria-live="polite">loading</div>
  <div id="help-text"></div>
  <section id="onboarding" aria-live="polite"></section>
  <section id="progress" aria-live="polite">
    <div class="progress-panel">
      <div class="progress-line"><span id="progress-phase">embedding images</span><span id="progress-count">0 / 0</span></div>
      <div class="bar"><div id="progress-fill" class="bar-fill"></div></div>
      <div id="starfield" class="starfield" aria-hidden="true"></div>
      <div id="progress-log" class="progress-log" aria-live="polite"></div>
    </div>
  </section>
  <section id="menu" aria-hidden="true">
    <div class="menu-panel" role="dialog" aria-label="menu">
      <button type="button" data-menu="reset-camera">reset camera</button>
      <button type="button" data-menu="fit-constellation">fit constellation</button>
      <button type="button" data-menu="reimport">reimport last folder</button>
      <button type="button" data-menu="open-data">open data folder</button>
      <button type="button" data-menu="clear-data">clear data</button>
      <button type="button" data-menu="debug">debug status</button>
      <button type="button" data-menu="close">close</button>
      <pre id="debug" class="debug"></pre>
    </div>
  </section>
  <script type="module">
    const status = document.querySelector('#status');
    const helpText = document.querySelector('#help-text');
    const root = document.querySelector('#viewer');
    const onboarding = document.querySelector('#onboarding');
    const progress = document.querySelector('#progress');
    const progressPhase = document.querySelector('#progress-phase');
    const progressCount = document.querySelector('#progress-count');
    const progressFill = document.querySelector('#progress-fill');
    const starfield = document.querySelector('#starfield');
    const progressLog = document.querySelector('#progress-log');
    const menu = document.querySelector('#menu');
    const debug = document.querySelector('#debug');
    const desktop = window.constellationDesktop;
    let viewerInstance = null;
    let latestStatus = null;
    let starCount = 0;
    let targetStarCount = 0;
    let lastProgressLogKey = '';
    let visibleProgress = 0;
    let helpTimer = 0;
    let idleTimer = 0;
    let spinnerFrame = 0;
    let wasPointerLocked = false;
    let tutorialActive = false;
    let tutorialTransitioning = false;
    let tutorialIndex = 0;
    const verticalTutorialKeys = new Set();
    const tutorialSteps = [
      { id: 'move', text: 'use <kbd>W</kbd>/<kbd>A</kbd>/<kbd>S</kbd>/<kbd>D</kbd> to move around' },
      { id: 'look', text: 'move your <span class="mouse-icon">🖱</span> mouse to move your view' },
      { id: 'vertical', text: 'use <kbd>space</kbd> and <kbd>C</kbd> to go up and down' },
      { id: 'slow', text: 'use <kbd>shift</kbd> to go slower' },
      { id: 'menu', text: 'press <kbd>esc</kbd> to see the menu' },
    ];
    const spinnerFrames = ['⠁', '⠂', '⠄', '⡀', '⢀', '⠠'];
    const progressLogEntries = [];
    window.setInterval(() => {
      if (!progress.classList.contains('visible') || progressLogEntries.length === 0) return;
      spinnerFrame = (spinnerFrame + 1) % spinnerFrames.length;
      renderProgressLog();
    }, 140);

    const [payload, sourcesPayload, initialStatus] = await Promise.all([
      fetch('/api/assets?limit=5000').then((response) => {
        if (!response.ok) throw new Error(`assets HTTP ${response.status}`);
        return response.json();
      }),
      fetch('/api/sources').then((response) => response.ok ? response.json() : { sources: [] }),
      fetch('/api/status').then((response) => response.ok ? response.json() : null),
    ]);
    const assets = payload.assets ?? [];
    const sources = sourcesPayload.sources ?? [];
    latestStatus = initialStatus;
    const data = { images: assets.map((asset) => ({ ...asset, url: asset.fullUrl ?? asset.thumbnailUrl })) };

    async function importViewer() {
      const candidates = [
        '/viewer-entry.js',
        '/viewer/constellation-viewer.js',
        '/viewer/constellation-viewer.es.js',
        '/viewer/constellation-viewer.mjs',
        '/viewer/viewer.js',
        '/viewer/viewer.mjs',
        '/viewer/index.js',
        '/viewer/index.mjs',
      ];
      for (const url of candidates) {
        try { return await import(url); } catch (_) {}
      }
      return null;
    }

    const viewer = await importViewer();
    if (viewer && typeof viewer.mount === 'function') {
      status.textContent = assets.length ? `${assets.length} images` : '';
      viewerInstance = viewer.mount(root, data, { backgroundColor: 0x000000, sprites: { renderMode: 'auto' } });
      requestAnimationFrame(() => root.classList.add('visible'));
    } else {
      status.textContent = 'viewer bundle missing';
      root.className = 'fallback';
      root.innerHTML = '<p>viewer bundle missing</p><div class="grid"></div>';
      renderFallbackGrid(assets);
    }

    if (assets.length === 0) showOnboarding();
    else startTutorial();
    scheduleIdleHelp();
    ['pointerdown', 'wheel', 'keydown'].forEach((type) => {
      window.addEventListener(type, noteActivity, { passive: true });
    });
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
      if (!tutorialActive) showHelp('wasd move · spacebar up · c down · esc go back', 6000);
    });

    document.addEventListener('pointerlockchange', () => {
      const canvas = root.querySelector('canvas');
      const isPointerLocked = document.pointerLockElement === canvas;
      if (wasPointerLocked && !isPointerLocked && isPlayviewVisible() && !menu.classList.contains('visible')) {
        completeTutorialStep('menu');
        showMenu();
        if (!tutorialActive) showHelp('wasd move · spacebar up · c down · esc go back', 6000);
      }
      wasPointerLocked = isPointerLocked;
    });

    menu.addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-menu]');
      if (!button) return;
      const action = button.dataset.menu;
      if (action === 'close') hideMenu();
      if (action === 'reset-camera') { viewerInstance?.resetCamera?.(); hideMenu(); }
      if (action === 'fit-constellation') { viewerInstance?.fitToContent?.(); hideMenu(); startTutorial(); }
      if (action === 'reimport') await reimportLastFolder();
      if (action === 'open-data') await postJson('/api/system/open-data-dir', {});
      if (action === 'clear-data') await clearData();
      if (action === 'debug') await showDebug();
    });

    function showOnboarding() {
      onboarding.innerHTML = onboardingMarkup();
      onboarding.classList.add('visible');
      requestAnimationFrame(() => onboarding.classList.add('fade-in'));
      const dropZone = onboarding.querySelector('.drop-zone');
      dropZone?.addEventListener('click', chooseFolder);
      dropZone?.addEventListener('dragenter', (event) => { event.preventDefault(); dropZone.classList.add('dragging'); });
      dropZone?.addEventListener('dragover', (event) => { event.preventDefault(); dropZone.classList.add('dragging'); });
      dropZone?.addEventListener('dragleave', () => dropZone.classList.remove('dragging'));
      dropZone?.addEventListener('drop', handleDrop);
      onboarding.querySelector('[data-action="choose-studio"]')?.addEventListener('click', chooseStudioDataset);
      onboarding.querySelector('[data-manual-form="folder"]')?.addEventListener('submit', submitManualFolder);
      onboarding.querySelector('[data-manual-form="studio"]')?.addEventListener('submit', submitManualStudio);
    }

    function onboardingMarkup() {
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

    async function handleDrop(event) {
      event.preventDefault();
      event.currentTarget.classList.remove('dragging');
      const path = [...(event.dataTransfer?.files ?? [])].map((file) => file.path).find(Boolean);
      if (path) {
        await startFolderImport(path);
        return;
      }
      status.textContent = 'directory path unavailable; opening picker';
      await chooseFolder();
    }

    async function chooseFolder() {
      if (desktop?.openImportFolder) {
        await runDesktopImport(() => desktop.openImportFolder(), 'importing directory');
        return;
      }
      const path = await chooseBackendPath('/api/dialog/folder', 'choose a directory');
      if (path) {
        await startFolderImport(path);
        return;
      }
      onboarding.querySelector('[data-manual-form="folder"]')?.classList.add('visible');
      onboarding.querySelector('[data-manual-form="folder"] input')?.focus();
    }

    async function chooseStudioDataset() {
      if (desktop?.openImportStudio) {
        await runDesktopImport(() => desktop.openImportStudio(), 'importing dataset');
        return;
      }
      const path = await chooseBackendPath('/api/dialog/studio', 'choose a dataset');
      if (path) {
        await submitImportPath('/api/import/studio', path, 'opening dataset');
        return;
      }
      onboarding.querySelector('[data-manual-form="studio"]')?.classList.add('visible');
      onboarding.querySelector('[data-manual-form="studio"] input')?.focus();
    }

    async function chooseBackendPath(endpoint, message) {
      status.textContent = message;
      try {
        const response = await postJson(endpoint, {});
        if (response.ok && typeof response.path === 'string') return response.path;
        status.textContent = 'no path selected';
        return null;
      } catch (error) {
        status.textContent = `picker failed: ${error instanceof Error ? error.message : String(error)}`;
        return null;
      }
    }

    async function runDesktopImport(importer, message) {
      showProgress({ jobPhase: message, jobCompleted: 0, jobTotal: 0 });
      try {
        const result = await importer();
        if (result?.ok) window.location.reload();
        else if (!result?.canceled) status.textContent = result?.error ?? 'import canceled';
      } catch (error) {
        status.textContent = `import failed: ${error instanceof Error ? error.message : String(error)}`;
      } finally {
        hideProgress();
      }
    }

    async function submitManualFolder(event) {
      event.preventDefault();
      const path = new FormData(event.currentTarget).get('path');
      if (typeof path !== 'string' || !path.trim()) return;
      await startFolderImport(path);
    }

    async function submitManualStudio(event) {
      event.preventDefault();
      const path = new FormData(event.currentTarget).get('path');
      if (typeof path !== 'string' || !path.trim()) return;
      await submitImportPath('/api/import/studio', path, 'opening dataset');
    }

    async function startFolderImport(path) {
      showProgress({ jobPhase: 'queued', jobCompleted: 0, jobTotal: 0 });
      const response = await fetch('/api/import/folder', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ path, background: true }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        hideProgress();
        status.textContent = `error — ${result.error ?? response.statusText}`;
        return;
      }
      onboarding.classList.remove('fade-in');
      onboarding.classList.remove('visible');
      await pollImportProgress();
    }

    async function submitImportPath(endpoint, path, label) {
      status.textContent = label;
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        status.textContent = `error — ${result.error ?? response.statusText}`;
        return;
      }
      window.location.reload();
    }

    async function pollImportProgress() {
      while (true) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        const current = await fetch('/api/status').then((response) => response.json());
        latestStatus = current;
        showProgress(current);
        if (current.state === 'error' || current.jobPhase === 'error') {
          hideProgress();
          status.textContent = `error — ${current.jobMessage ?? 'import failed'}`;
          return;
        }
        if ((current.jobPhase === 'ready' || current.state === 'idle') && current.totalAssets > 0) {
          window.location.reload();
          return;
        }
      }
    }

    function showProgress(current) {
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

    function nextVisibleProgress(current, serverCompleted, total, phase) {
      if (total <= 0) return Math.max(current, serverCompleted);
      if (serverCompleted >= total || phase === 'ready' || phase === 'layout') return serverCompleted;
      const floor = Math.max(current, serverCompleted);
      const trickleCap = Math.min(total - 1, serverCompleted + Math.max(1, total * 0.08));
      return Math.min(trickleCap, floor + Math.max(0.18, total * 0.004));
    }

    function maybeLogProgress(phase, completed, total, message) {
      const key = `${phase}:${completed}:${total}:${message}`;
      if (key === lastProgressLogKey) return;
      lastProgressLogKey = key;
      const count = total > 0 ? ` ${completed}/${total}` : '';
      const suffix = message ? ` — ${message}` : '';
      progressLogEntries.unshift(`${phase}${count}${suffix}`);
      progressLogEntries.length = Math.min(progressLogEntries.length, 8);
      renderProgressLog();
    }

    function renderProgressLog() {
      progressLog.replaceChildren(...progressLogEntries.slice(0, 4).map((entry, index) => {
        const line = document.createElement('div');
        line.className = 'progress-log-line';
        line.textContent = index === 0 ? `${spinnerFrames[spinnerFrame]} ${entry}` : `  ${entry}`;
        return line;
      }));
    }

    function hideProgress() {
      progress.classList.remove('visible');
      targetStarCount = 0;
    }

    function drainStarQueue() {
      if (!progress.classList.contains('visible')) return;
      if (starCount < targetStarCount) addStar();
      if (starCount < targetStarCount) window.setTimeout(drainStarQueue, 38);
    }

    function addStar() {
      starCount += 1;
      const star = document.createElement('span');
      star.className = 'star';
      const angle = (starCount * 137.508) * Math.PI / 180;
      const radius = Math.sqrt(starCount / 160) * 44;
      const jitterX = (Math.random() - .5) * 10;
      const jitterY = (Math.random() - .5) * 10;
      star.style.left = `${50 + Math.cos(angle) * radius + jitterX}%`;
      star.style.top = `${50 + Math.sin(angle) * radius + jitterY}%`;
      starfield.append(star);
    }

    function isPlayviewVisible() {
      return assets.length > 0
        && !onboarding.classList.contains('visible')
        && !progress.classList.contains('visible');
    }

    function startTutorial() {
      if (!isPlayviewVisible()) return;
      tutorialActive = true;
      tutorialTransitioning = false;
      tutorialIndex = 0;
      verticalTutorialKeys.clear();
      showTutorialStep();
    }

    function showTutorialStep() {
      if (!tutorialActive || !isPlayviewVisible()) return;
      const step = tutorialSteps[tutorialIndex];
      if (!step) {
        tutorialActive = false;
        hideHelp();
        return;
      }
      helpText.innerHTML = step.text;
      helpText.classList.add('visible');
      window.clearTimeout(helpTimer);
    }

    function completeTutorialStep(stepId) {
      const step = tutorialSteps[tutorialIndex];
      if (!tutorialActive || tutorialTransitioning || step?.id !== stepId) return;
      tutorialTransitioning = true;
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
      }, 1500);
    }

    function handleTutorialKey(event) {
      if (!tutorialActive || tutorialTransitioning) return;
      const step = tutorialSteps[tutorialIndex];
      if (step?.id === 'move' && ['KeyW', 'KeyA', 'KeyS', 'KeyD'].includes(event.code)) {
        completeTutorialStep('move');
      }
      if (step?.id === 'vertical' && ['Space', 'KeyC'].includes(event.code)) {
        verticalTutorialKeys.add(event.code);
        if (verticalTutorialKeys.has('Space') && verticalTutorialKeys.has('KeyC')) {
          completeTutorialStep('vertical');
        }
      }
      if (step?.id === 'slow' && ['ShiftLeft', 'ShiftRight'].includes(event.code)) {
        completeTutorialStep('slow');
      }
      if (step?.id === 'menu' && event.key === 'Escape') {
        completeTutorialStep('menu');
      }
    }

    function handleTutorialPointerMove(event) {
      const moved = Math.abs(event.movementX ?? 0) + Math.abs(event.movementY ?? 0) > 0;
      if (tutorialActive && !tutorialTransitioning && tutorialSteps[tutorialIndex]?.id === 'look' && moved) {
        completeTutorialStep('look');
      }
    }

    function showHelp(message, duration = 4200) {
      if (!isPlayviewVisible() || tutorialActive) return;
      helpText.textContent = message;
      helpText.classList.add('visible');
      window.clearTimeout(helpTimer);
      helpTimer = window.setTimeout(() => helpText.classList.remove('visible'), duration);
    }

    function hideHelp() {
      if (menu.classList.contains('visible') || tutorialActive) return;
      helpText.classList.remove('visible');
      window.clearTimeout(helpTimer);
    }

    function scheduleIdleHelp() {
      window.clearTimeout(idleTimer);
      idleTimer = window.setTimeout(() => showHelp('wasd move · spacebar up · c down · esc go back', 6000), 5000);
    }

    function noteActivity() {
      if (!tutorialActive) hideHelp();
      scheduleIdleHelp();
    }

    function toggleMenu() {
      if (menu.classList.contains('visible')) hideMenu();
      else showMenu();
    }

    function showMenu() {
      menu.classList.add('visible');
      menu.setAttribute('aria-hidden', 'false');
      menu.querySelector('button')?.focus();
    }

    function hideMenu() {
      menu.classList.remove('visible');
      menu.setAttribute('aria-hidden', 'true');
      debug.classList.remove('visible');
      root.querySelector('canvas')?.focus();
    }

    async function clearData() {
      if (!confirm('clear all local constellation data?')) return;
      await postJson('/api/data/clear', {});
      window.location.reload();
    }

    async function reimportLastFolder() {
      const current = latestStatus ?? await fetch('/api/status').then((response) => response.json());
      const path = current.lastImportPath;
      if (!path) {
        status.textContent = 'no last folder';
        return;
      }
      hideMenu();
      await startFolderImport(path);
    }

    async function showDebug() {
      const current = await fetch('/api/status').then((response) => response.json());
      latestStatus = current;
      debug.textContent = JSON.stringify(current, null, 2);
      debug.classList.toggle('visible');
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok || result.ok === false) throw new Error(result.error ?? response.statusText);
      return result;
    }

    function renderFallbackGrid(assets) {
      const grid = root.querySelector('.grid');
      if (!grid) return;
      for (const asset of assets) {
        const card = document.createElement('article');
        card.className = 'card';
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.src = asset.thumbnailUrl;
        img.alt = asset.id;
        const caption = document.createElement('div');
        caption.textContent = asset.metadata?.sourcePath ?? asset.id;
        card.append(img, caption);
        grid.append(card);
      }
    }
  </script>
</body>
</html>
"""


@dataclass(frozen=True, slots=True)
class BackendConfig:
    """Local backend configuration."""

    host: str
    port: int
    data_dir: Path
    viewer_dist: Path | None = None
    embedding_engine: str = "none"
    embedding_model: str = DEFAULT_MODEL
    embedding_pretrained: str = DEFAULT_PRETRAINED
    embedding_device: str = "auto"
    embedding_batch_size: int = 32
    onnx_model: Path | None = None
    onnx_provider: str = "auto"


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with reusable sockets."""

    allow_reuse_address: bool = True


class BackendRequestHandler(BaseHTTPRequestHandler):
    """Request handler configured by ``make_handler``."""

    store: ClassVar[IndexStore]
    asset_root: ClassVar[Path]
    viewer_dist: ClassVar[Path | None]
    embedding_provider: ClassVar[EmbeddingProvider | None]
    embedding_batch_size: ClassVar[int]
    import_lock: ClassVar[threading.Lock]
    import_thread: ClassVar[threading.Thread | None]

    def do_OPTIONS(self) -> None:
        """Serve CORS preflight requests for local development."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        """Serve a GET request."""
        self._serve_get(send_body=True)

    def do_HEAD(self) -> None:
        """Serve a HEAD request."""
        self._serve_get(send_body=False)

    def do_POST(self) -> None:  # noqa: C901, PLR0911
        """Serve local API mutation requests."""
        route = urlsplit(self.path).path
        try:
            if route == "/api/import/folder":
                self._post_import_folder()
                return
            if route == "/api/import/studio":
                self._post_import_studio()
                return
            if route == "/api/dialog/folder":
                self._post_dialog_folder()
                return
            if route == "/api/dialog/studio":
                self._post_dialog_studio()
                return
            if route == "/api/data/clear":
                self._post_clear_data()
                return
            if route == "/api/system/open-data-dir":
                self._post_open_data_dir()
                return
            if route == "/api/index/start":
                self.store.set_index_state("idle")
                self._send_json({"ok": True, "status": self.store.status()})
                return
            if route == "/api/index/pause":
                self.store.set_paused(paused=True)
                self._send_json({"ok": True, "status": self.store.status()})
                return
            if route == "/api/index/resume":
                self.store.set_paused(paused=False)
                self._send_json({"ok": True, "status": self.store.status()})
                return
        except (
            OSError,
            RuntimeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self._send_json(
                {"ok": False, "error": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Write compact access logs to stderr."""
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)

    def _serve_get(self, *, send_body: bool) -> None:  # noqa: C901, PLR0911
        split = urlsplit(self.path)
        route = split.path
        query = parse_qs(split.query)
        if route in {"/", "/index.html"}:
            self._send_bytes(
                BACKEND_INDEX_HTML.encode("utf-8"),
                "text/html; charset=utf-8",
                send_body=send_body,
            )
            return
        if route == "/api/status":
            self._send_json(self.store.status(), send_body=send_body)
            return
        if route == "/api/sources":
            self._send_json(source_capabilities(), send_body=send_body)
            return
        if route == "/api/assets":
            self._get_assets(query, send_body=send_body)
            return
        if route == "/api/assets/near":
            self._get_near_assets(query, send_body=send_body)
            return
        if route.startswith("/api/assets/"):
            self._get_asset(
                route.removeprefix("/api/assets/"), send_body=send_body
            )
            return
        if route.startswith("/api/thumbnails/"):
            self._send_asset_file(
                route.removeprefix("/api/thumbnails/"),
                thumbnail=True,
                send_body=send_body,
            )
            return
        if route.startswith("/api/files/"):
            self._send_asset_file(
                route.removeprefix("/api/files/"),
                thumbnail=False,
                send_body=send_body,
            )
            return
        if route == "/viewer-entry.js" and self.viewer_dist is not None:
            self._send_viewer_entry(send_body=send_body)
            return
        if route.startswith("/viewer/") and self.viewer_dist is not None:
            path = resolve_below(
                self.viewer_dist,
                route.removeprefix("/viewer/"),
            )
            self._send_path_or_404(path, send_body=send_body)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _post_import_folder(self) -> None:
        payload = self._read_json_body()
        folder_obj = payload.get("path")
        if not isinstance(folder_obj, str) or not folder_obj:
            msg = "JSON body must include a non-empty string path"
            raise ValueError(msg)
        if payload.get("background") is True:
            self._start_background_import_folder(Path(folder_obj))
            self._send_json(
                {
                    "ok": True,
                    "started": True,
                    "status": self.store.status(),
                },
            )
            return
        result = self._run_import_folder(Path(folder_obj))
        self._send_json(
            {
                "ok": True,
                "imported": result.imported,
                "totalAssets": result.total_assets,
                "sourceType": result.source_type,
                "sourceId": result.source_id,
                "folder": str(result.folder),
                "status": self.store.status(),
            },
        )

    def _run_import_folder(self, folder: Path) -> ImportResult:
        return import_folder(
            folder,
            store=self.store,
            asset_root=self.asset_root,
            embedding_provider=self.embedding_provider,
            batch_size=self.embedding_batch_size,
        )

    def _start_background_import_folder(self, folder: Path) -> None:
        with self.import_lock:
            if (
                self.import_thread is not None
                and self.import_thread.is_alive()
            ):
                msg = "an import job is already running"
                raise RuntimeError(msg)
            self.store.set_index_state("importing")
            self.store.set_job_progress(
                phase="queued",
                completed=0,
                total=0,
                message=f"Queued import for {folder}",
            )
            thread = threading.Thread(
                target=self._background_import_folder,
                args=(folder,),
                daemon=True,
            )
            type(self).import_thread = thread
            thread.start()

    def _background_import_folder(self, folder: Path) -> None:
        try:
            self._run_import_folder(folder)
        except (OSError, RuntimeError, ValueError) as exc:
            self.store.set_index_state("error")
            self.store.set_job_progress(
                phase="error",
                completed=0,
                total=0,
                message=str(exc),
            )
        finally:
            with self.import_lock:
                type(self).import_thread = None

    def _post_clear_data(self) -> None:
        _ = self._read_json_body()
        with self.import_lock:
            if (
                self.import_thread is not None
                and self.import_thread.is_alive()
            ):
                msg = "cannot clear data while an import job is running"
                raise RuntimeError(msg)
            self.store.clear_assets()
            if self.asset_root.exists():
                shutil.rmtree(self.asset_root)
            self.asset_root.mkdir(parents=True, exist_ok=True)
        self._send_json({"ok": True, "status": self.store.status()})

    def _post_open_data_dir(self) -> None:
        _ = self._read_json_body()
        data_dir = self.asset_root.parent
        data_dir.mkdir(parents=True, exist_ok=True)
        open_path_in_file_manager(data_dir)
        self._send_json({"ok": True, "path": str(data_dir)})

    def _post_dialog_folder(self) -> None:
        _ = self._read_json_body()
        path = choose_folder_dialog()
        self._send_json(
            {"ok": path is not None, "path": str(path) if path else None},
        )

    def _post_dialog_studio(self) -> None:
        _ = self._read_json_body()
        path = choose_studio_dataset_dialog()
        self._send_json(
            {"ok": path is not None, "path": str(path) if path else None},
        )

    def _post_import_studio(self) -> None:
        payload = self._read_json_body()
        path_obj = payload.get("path")
        if not isinstance(path_obj, str) or not path_obj:
            msg = "JSON body must include a non-empty string path"
            raise ValueError(msg)
        asset_dir_obj = payload.get("assetDir")
        if asset_dir_obj is not None and not isinstance(asset_dir_obj, str):
            msg = "assetDir must be a string when provided"
            raise ValueError(msg)
        result = import_studio_dataset(
            Path(path_obj),
            store=self.store,
            asset_dir=Path(asset_dir_obj) if asset_dir_obj else None,
        )
        self._send_json(
            {
                "ok": True,
                "imported": result.imported,
                "totalAssets": result.total_assets,
                "sourceType": result.source_type,
                "sourceId": result.source_id,
                "dataset": str(result.data_json),
                "assetRoot": str(result.image_root),
                "status": self.store.status(),
            },
        )

    def _get_assets(
        self,
        query: Mapping[str, list[str]],
        *,
        send_body: bool,
    ) -> None:
        limit = bounded_int(query, "limit", default=DEFAULT_LIMIT)
        offset = max(
            0, bounded_int(query, "offset", default=0, upper=1_000_000_000)
        )
        assets = self.store.list_assets(limit=limit, offset=offset)
        self._send_json(
            {
                "assets": assets,
                "limit": limit,
                "offset": offset,
                "total": self.store.count_assets(),
            },
            send_body=send_body,
        )

    def _get_near_assets(
        self,
        query: Mapping[str, list[str]],
        *,
        send_body: bool,
    ) -> None:
        x = query_float(query, "x", 0.0)
        y = query_float(query, "y", 0.0)
        z = query_float(query, "z", 0.0)
        radius = query_float(query, "radius", 50.0)
        limit = bounded_int(query, "limit", default=DEFAULT_LIMIT)
        assets = self.store.nearby_assets(
            point=(x, y, z), radius=radius, limit=limit
        )
        self._send_json(
            {"assets": assets, "total": len(assets)}, send_body=send_body
        )

    def _get_asset(self, raw_id: str, *, send_body: bool) -> None:
        asset = self.store.get_asset(unquote(raw_id))
        if asset is None:
            self.send_error(HTTPStatus.NOT_FOUND, "asset not found")
            return
        self._send_json(asset, send_body=send_body)

    def _send_asset_file(
        self,
        raw_id: str,
        *,
        thumbnail: bool,
        send_body: bool,
    ) -> None:
        asset_id = unquote(raw_id)
        path = (
            self.store.asset_thumbnail_path(asset_id)
            if thumbnail
            else self.store.asset_file_path(asset_id)
        )
        self._send_path_or_404(path, send_body=send_body)

    def _send_viewer_entry(self, *, send_body: bool) -> None:
        if self.viewer_dist is None:
            self.send_error(HTTPStatus.NOT_FOUND, "viewer dist not configured")
            return
        entry = find_viewer_entry_file(self.viewer_dist)
        if entry is None:
            self.send_error(HTTPStatus.NOT_FOUND, "viewer entry not found")
            return
        relative = entry.relative_to(self.viewer_dist)
        module = (
            f"export * from {json.dumps('/viewer/' + relative.as_posix())};\n"
        )
        self._send_bytes(
            module.encode("utf-8"),
            "text/javascript; charset=utf-8",
            send_body=send_body,
        )

    def _send_path_or_404(self, path: Path | None, *, send_body: bool) -> None:
        if path is None or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        content_type = (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self._send_bytes(path.read_bytes(), content_type, send_body=send_body)

    def _send_json(
        self,
        payload: object,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        send_body: bool = True,
    ) -> None:
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n",
            "application/json; charset=utf-8",
            status=status,
            send_body=send_body,
        )

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        send_body: bool,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _read_json_body(self) -> Mapping[str, object]:
        length_header = self.headers.get("Content-Length", "0")
        length = int(length_header)
        loaded: object = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(loaded, Mapping):
            msg = "request body must be a JSON object"
            raise ValueError(msg)
        return cast("Mapping[str, object]", loaded)


def bounded_int(
    query: Mapping[str, list[str]],
    key: str,
    *,
    default: int,
    upper: int = MAX_LIMIT,
) -> int:
    """Parse a bounded positive integer query parameter."""
    raw = query.get(key, [str(default)])[0]
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return min(max(parsed, 0), upper)


def query_float(
    query: Mapping[str, list[str]],
    key: str,
    default: float,
) -> float:
    """Parse a float query parameter."""
    raw = query.get(key, [str(default)])[0]
    try:
        return float(raw)
    except ValueError:
        return default


def open_path_in_file_manager(path: Path) -> None:
    """Reveal a local directory in the platform file manager."""
    system = platform.system().lower()
    if system == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        return
    if system == "windows":
        subprocess.Popen(["explorer", str(path)])  # noqa: S603, S607
        return
    subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


def choose_folder_dialog() -> Path | None:
    """Open a native-ish folder dialog and return the selected path."""
    system = platform.system().lower()
    if system == "darwin":
        return run_osascript_path(
            'POSIX path of (choose folder with prompt "Choose photo directory")',
        )
    if system == "windows":
        script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Choose photo directory'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  Write-Output $dialog.SelectedPath
}
"""
        return run_powershell_path(script)
    return run_tk_folder_dialog()


def choose_studio_dataset_dialog() -> Path | None:
    """Open a native-ish file dialog for Studio JSON datasets."""
    system = platform.system().lower()
    if system == "darwin":
        script = (
            'POSIX path of (choose file with prompt "Choose Constellation Studio dataset" '
            'of type {"json"})'
        )
        return run_osascript_path(script)
    if system == "windows":
        script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Choose Constellation Studio dataset'
$dialog.Filter = 'JSON files (*.json)|*.json|All files (*.*)|*.*'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  Write-Output $dialog.FileName
}
"""
        return run_powershell_path(script)
    return run_tk_file_dialog()


def run_osascript_path(script: str) -> Path | None:
    """Run an AppleScript path chooser and return its POSIX path."""
    try:
        result = subprocess.run(  # noqa: S603
            ["osascript", "-e", script],  # noqa: S607
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return run_tk_folder_dialog()
    if result.returncode != 0:
        return None
    selected = result.stdout.strip()
    return Path(selected).expanduser().resolve() if selected else None


def run_powershell_path(script: str) -> Path | None:
    """Run a PowerShell path chooser and return its path."""
    executable = (
        "powershell.exe" if platform.system().lower() == "windows" else "pwsh"
    )
    try:
        result = subprocess.run(  # noqa: S603
            [executable, "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    selected = result.stdout.strip().splitlines()
    if not selected:
        return None
    return Path(selected[-1]).expanduser().resolve()


def run_tk_folder_dialog() -> Path | None:
    """Run a Tk folder dialog when platform dialogs are unavailable."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(title="Choose photo directory")
    finally:
        root.destroy()
    return Path(selected).expanduser().resolve() if selected else None


def run_tk_file_dialog() -> Path | None:
    """Run a Tk file dialog when platform dialogs are unavailable."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askopenfilename(
            title="Choose Constellation Studio dataset",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
    finally:
        root.destroy()
    return Path(selected).expanduser().resolve() if selected else None


def source_capabilities() -> dict[str, object]:
    """Return BYO source types surfaced by the onboarding UI."""
    return {
        "sources": [
            {
                "type": "folder",
                "label": "Photo directory",
                "enabled": True,
                "importEndpoint": "/api/import/folder",
                "description": (
                    "Import images recursively from a local directory, "
                    "camera dump, or exported photo folder."
                ),
            },
            {
                "type": "studioDataset",
                "label": "Constellation Studio dataset",
                "enabled": True,
                "importEndpoint": "/api/import/studio",
                "description": (
                    "Open a constellation.json or constellation.studio.json "
                    "set produced by Studio."
                ),
            },
        ],
    }


def resolve_below(root: Path, route_tail: str) -> Path | None:
    """Resolve a URL tail below a trusted root, rejecting traversal."""
    resolved_root = root.expanduser().resolve()
    decoded = unquote(route_tail)
    parts = [part for part in decoded.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return None
    candidate = resolved_root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        return None
    return candidate


def find_default_viewer_dist(start: Path) -> Path | None:
    """Find packages/viewer/dist from a repo descendant."""
    for base in [start, *start.parents]:
        candidate = base / "packages" / "viewer" / "dist"
        if candidate.is_dir():
            return candidate
    return None


def find_viewer_entry_file(viewer_dist: Path) -> Path | None:
    """Return the likely ESM viewer entry file."""
    names = [
        "constellation-viewer.js",
        "constellation-viewer.es.js",
        "constellation-viewer.mjs",
        "viewer.js",
        "viewer.mjs",
        "index.js",
        "index.mjs",
    ]
    for name in names:
        candidate = viewer_dist / name
        if candidate.is_file():
            return candidate
    candidates = sorted(
        [
            path
            for pattern in ("*.mjs", "*.js")
            for path in viewer_dist.glob(pattern)
            if path.is_file()
        ],
        key=lambda path: path.name,
    )
    return candidates[0] if candidates else None


def make_handler(
    *,
    store: IndexStore,
    asset_root: Path,
    viewer_dist: Path | None,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_batch_size: int = 32,
) -> type[BackendRequestHandler]:
    """Return a request handler bound to a store and paths."""

    class ConfiguredBackendRequestHandler(BackendRequestHandler):
        pass

    ConfiguredBackendRequestHandler.store = store
    ConfiguredBackendRequestHandler.asset_root = (
        asset_root.expanduser().resolve()
    )
    ConfiguredBackendRequestHandler.viewer_dist = (
        viewer_dist.expanduser().resolve() if viewer_dist is not None else None
    )
    ConfiguredBackendRequestHandler.embedding_provider = embedding_provider
    ConfiguredBackendRequestHandler.embedding_batch_size = embedding_batch_size
    ConfiguredBackendRequestHandler.import_lock = threading.Lock()
    ConfiguredBackendRequestHandler.import_thread = None
    return ConfiguredBackendRequestHandler


def server_url(server: TCPServer, host: str) -> str:
    """Return the URL for a bound HTTP server."""
    port = int(server.server_address[1])
    return f"http://{host}:{port}/"


def build_parser() -> argparse.ArgumentParser:
    """Build the backend CLI parser."""
    parser = argparse.ArgumentParser(
        description="Run the Constellation local backend API."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host.")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Bind port; use 0 for ephemeral.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".constellation-backend"),
        help="App data directory for SQLite and generated assets.",
    )
    parser.add_argument(
        "--viewer-dist",
        type=Path,
        default=None,
        help="Optional built @constellation/viewer dist directory.",
    )
    parser.add_argument(
        "--embedding-engine",
        default="none",
        help="Embedding engine for folder imports: none, openclip, or onnx.",
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_MODEL,
        help=f"OpenCLIP model name (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--embedding-pretrained",
        default=DEFAULT_PRETRAINED,
        help=f"OpenCLIP pretrained tag (default: {DEFAULT_PRETRAINED}).",
    )
    parser.add_argument(
        "--embedding-device",
        default="auto",
        help="OpenCLIP device: auto, cpu, mps, cuda.",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=8,
        help="Images per embedding batch.",
    )
    parser.add_argument(
        "--onnx-model",
        type=Path,
        default=None,
        help="Path to ONNX image encoder for --embedding-engine=onnx.",
    )
    parser.add_argument(
        "--onnx-provider",
        default="auto",
        help="ONNX Runtime provider: auto, cpu, cuda, directml, coreml.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the local app in the default browser after startup.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    """Run the local backend until interrupted."""
    viewer_dist_arg = cast("Path | None", args.viewer_dist)
    config = BackendConfig(
        host=str(args.host),
        port=int(args.port),
        data_dir=Path(args.data_dir),
        viewer_dist=viewer_dist_arg,
        embedding_engine=str(args.embedding_engine),
        embedding_model=str(args.embedding_model),
        embedding_pretrained=str(args.embedding_pretrained),
        embedding_device=str(args.embedding_device),
        embedding_batch_size=int(args.embedding_batch_size),
        onnx_model=cast("Path | None", args.onnx_model),
        onnx_provider=str(args.onnx_provider),
    )
    paths = default_indexing_paths(config.data_dir)
    viewer_dist = config.viewer_dist or find_default_viewer_dist(Path.cwd())
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    embedding_provider = create_embedding_provider(
        engine=config.embedding_engine,
        model=config.embedding_model,
        pretrained=config.embedding_pretrained,
        device=config.embedding_device,
        onnx_model=config.onnx_model,
        onnx_provider=config.onnx_provider,
    )
    if embedding_provider is not None:
        store.set_embedding_engine(embedding_provider.cache_namespace)
    else:
        store.set_embedding_engine("none")
    handler = make_handler(
        store=store,
        asset_root=paths.asset_root,
        viewer_dist=viewer_dist,
        embedding_provider=embedding_provider,
        embedding_batch_size=config.embedding_batch_size,
    )
    with QuietThreadingHTTPServer(
        (config.host, config.port), handler
    ) as httpd:
        url = server_url(httpd, config.host)
        print(f"Constellation backend listening at {url}", flush=True)
        print(f"SQLite: {paths.db_path}", flush=True)
        print(f"Assets: {paths.asset_root}", flush=True)
        if bool(args.open):
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


@contextlib.contextmanager
def run_test_backend(
    *,
    data_dir: Path,
    viewer_dist: Path | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_batch_size: int = 32,
) -> Generator[str]:
    """Start an ephemeral backend server for tests."""
    paths = default_indexing_paths(data_dir)
    store = IndexStore(paths.db_path, asset_root=paths.asset_root)
    handler = make_handler(
        store=store,
        asset_root=paths.asset_root,
        viewer_dist=viewer_dist,
        embedding_provider=embedding_provider,
        embedding_batch_size=embedding_batch_size,
    )
    httpd = QuietThreadingHTTPServer((DEFAULT_HOST, 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield server_url(httpd, DEFAULT_HOST)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())

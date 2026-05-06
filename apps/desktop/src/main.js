import { app, BrowserWindow, Menu, dialog, ipcMain } from 'electron';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '../../..');

let backendProcess = null;
let backendUrl = null;
let mainWindow = null;

function viewerDistPath() {
  return process.env.CONSTELLATION_VIEWER_DIST ?? path.join(repoRoot, 'packages', 'viewer', 'dist');
}

function playviewDistPath() {
  return process.env.CONSTELLATION_PLAYVIEW_DIST ?? path.join(repoRoot, 'playview', 'dist');
}

function backendCommand() {
  if (process.env.CONSTELLATION_BACKEND_COMMAND) {
    return {
      command: process.env.CONSTELLATION_BACKEND_COMMAND,
      args: process.env.CONSTELLATION_BACKEND_ARGS?.split(' ') ?? [],
      shell: true,
    };
  }
  return {
    command: 'uv',
    args: [
      '--project',
      path.join(repoRoot, 'studio'),
      'run',
      'constellation-backend',
      '--host',
      '127.0.0.1',
      '--port',
      '0',
      '--data-dir',
      path.join(app.getPath('userData'), 'backend'),
      '--viewer-dist',
      viewerDistPath(),
      '--playview-dist',
      playviewDistPath(),
    ],
    shell: false,
  };
}

function startBackend() {
  if (process.env.CONSTELLATION_BACKEND_URL) {
    backendUrl = process.env.CONSTELLATION_BACKEND_URL;
    return Promise.resolve(backendUrl);
  }

  return new Promise((resolve, reject) => {
    const { command, args, shell } = backendCommand();
    const child = spawn(command, args, {
      cwd: repoRoot,
      env: process.env,
      shell,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    backendProcess = child;

    const failTimer = setTimeout(() => {
      reject(new Error('Timed out waiting for Constellation backend to start.'));
    }, 30_000);

    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      process.stdout.write(chunk);
      const match = chunk.match(/Constellation backend listening at (http:\/\/[^\s]+)/);
      if (match) {
        clearTimeout(failTimer);
        backendUrl = match[1];
        resolve(backendUrl);
      }
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => process.stderr.write(chunk));
    child.on('error', (error) => {
      clearTimeout(failTimer);
      reject(error);
    });
    child.on('exit', (code, signal) => {
      if (!backendUrl) {
        clearTimeout(failTimer);
        reject(new Error(`Backend exited before startup: code=${code} signal=${signal}`));
      }
    });
  });
}

async function createWindow() {
  const url = await startBackend();
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    title: 'Constellation',
    backgroundColor: '#050507',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadURL(process.env.CONSTELLATION_VIEWER_URL ?? url);
}

async function postImport(endpoint, filePath) {
  if (!backendUrl) throw new Error('Backend is not ready.');
  const response = await fetch(new URL(endpoint, backendUrl), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: filePath }),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(`Import failed: ${response.status} ${payload.error ?? response.statusText}`);
  }
  return payload;
}

async function importFolder(folderPath) {
  return postImport('/api/import/folder', folderPath);
}

async function importStudio(studioPath) {
  return postImport('/api/import/studio', studioPath);
}

async function chooseAndImportFolder() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Import photo directory',
    properties: ['openDirectory'],
  });
  if (result.canceled || result.filePaths.length === 0) return { ok: false, canceled: true };
  return importFolder(result.filePaths[0]);
}

async function chooseAndImportStudio() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Import Constellation Studio dataset',
    properties: ['openFile'],
    filters: [
      { name: 'Constellation Studio dataset', extensions: ['json'] },
      { name: 'All Files', extensions: ['*'] },
    ],
  });
  if (result.canceled || result.filePaths.length === 0) return { ok: false, canceled: true };
  return importStudio(result.filePaths[0]);
}

function installMenu() {
  const template = [
    {
      label: 'Constellation',
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'Library',
      submenu: [
        {
          label: 'Import Photo Directory…',
          accelerator: 'CmdOrCtrl+O',
          click: () => {
            chooseAndImportFolder()
              .then((result) => {
                if (result?.ok) mainWindow?.reload();
              })
              .catch((error) => {
                dialog.showErrorBox('Import failed', String(error));
              });
          },
        },
        {
          label: 'Import Studio Dataset…',
          accelerator: 'CmdOrCtrl+Shift+O',
          click: () => {
            chooseAndImportStudio()
              .then((result) => {
                if (result?.ok) mainWindow?.reload();
              })
              .catch((error) => {
                dialog.showErrorBox('Import failed', String(error));
              });
          },
        },
        { type: 'separator' },
        { role: 'reload' },
        { role: 'toggleDevTools' },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

ipcMain.handle('constellation:getBackendUrl', () => backendUrl);
ipcMain.handle('constellation:openImportFolder', async () => chooseAndImportFolder());
ipcMain.handle('constellation:openImportStudio', async () => chooseAndImportStudio());
ipcMain.handle('constellation:importFolder', async (_event, folderPath) => {
  if (typeof folderPath !== 'string') return null;
  return importFolder(folderPath);
});
ipcMain.handle('constellation:importStudio', async (_event, studioPath) => {
  if (typeof studioPath !== 'string') return null;
  return importStudio(studioPath);
});

app.whenReady().then(() => {
  installMenu();
  createWindow().catch((error) => {
    dialog.showErrorBox('Constellation failed to start', String(error));
    app.quit();
  });
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow().catch((error) => dialog.showErrorBox('Constellation failed to start', String(error)));
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  backendProcess?.kill();
  backendProcess = null;
});

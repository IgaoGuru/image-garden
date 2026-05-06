import { app, BrowserWindow, Menu, dialog, ipcMain } from 'electron';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '../../..');

let backendProcess = null;
let backendUrl = null;
let mainWindow = null;

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

async function importFolderFromDialog() {
  if (!backendUrl) return;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Import photo folder',
    properties: ['openDirectory'],
  });
  if (result.canceled || result.filePaths.length === 0) return;

  const response = await fetch(new URL('/api/import/folder', backendUrl), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: result.filePaths[0] }),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Import failed: ${response.status} ${text}`);
  }
  mainWindow?.reload();
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
          label: 'Import Folder…',
          accelerator: 'CmdOrCtrl+O',
          click: () => {
            importFolderFromDialog().catch((error) => {
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
ipcMain.handle('constellation:importFolder', async (_event, folderPath) => {
  if (typeof folderPath !== 'string' || !backendUrl) return null;
  const response = await fetch(new URL('/api/import/folder', backendUrl), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: folderPath }),
  });
  return response.json();
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

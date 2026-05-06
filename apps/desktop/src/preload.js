import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('constellationDesktop', {
  getBackendUrl: () => ipcRenderer.invoke('constellation:getBackendUrl'),
  importFolder: (folderPath) => ipcRenderer.invoke('constellation:importFolder', folderPath),
});

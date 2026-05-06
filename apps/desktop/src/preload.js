import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('constellationDesktop', {
  getBackendUrl: () => ipcRenderer.invoke('constellation:getBackendUrl'),
  openImportFolder: () => ipcRenderer.invoke('constellation:openImportFolder'),
  importFolder: (folderPath) => ipcRenderer.invoke('constellation:importFolder', folderPath),
});

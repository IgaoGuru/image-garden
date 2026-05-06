import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('constellationDesktop', {
  getBackendUrl: () => ipcRenderer.invoke('constellation:getBackendUrl'),
  openImportFolder: () => ipcRenderer.invoke('constellation:openImportFolder'),
  openImportStudio: () => ipcRenderer.invoke('constellation:openImportStudio'),
  importFolder: (folderPath) => ipcRenderer.invoke('constellation:importFolder', folderPath),
  importStudio: (studioPath) => ipcRenderer.invoke('constellation:importStudio', studioPath),
});

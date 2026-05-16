const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("erpDesktop", {
  getStatus: () => ipcRenderer.invoke("desktop:get-status"),
  setup: (options) => ipcRenderer.invoke("desktop:setup", options),
  start: () => ipcRenderer.invoke("desktop:start"),
  stop: () => ipcRenderer.invoke("desktop:stop"),
  openApp: () => ipcRenderer.invoke("desktop:open-app"),
  onLog: (callback) => {
    const listener = (_event, message) => callback(message);
    ipcRenderer.on("desktop:log", listener);
    return () => ipcRenderer.removeListener("desktop:log", listener);
  },
});

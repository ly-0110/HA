const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('iotDesktop',Object.freeze({
  request:input=>ipcRenderer.invoke('workbench:request',input),
  openArtifact:input=>ipcRenderer.invoke('workbench:open-artifact',input),
  previewArtifact:input=>ipcRenderer.invoke('workbench:preview-artifact',input),
  openDirectory:sessionId=>ipcRenderer.invoke('workbench:open-artifact',{sessionId,category:'directory'}),
  configureTool:(kind,candidate)=>ipcRenderer.invoke('workbench:configure-tool',kind,candidate),
  registerRoot:()=>ipcRenderer.invoke('workbench:register-root'),
  chooseWorkspace:()=>ipcRenderer.invoke('workbench:choose-workspace'),
  importWorkspace:()=>ipcRenderer.invoke('workbench:import-workspace'),
  status:()=>ipcRenderer.invoke('workbench:status'),
  retryBackend:()=>ipcRenderer.invoke('workbench:retry-backend'),
  openPreparation:kind=>ipcRenderer.invoke('workbench:open-preparation',kind),
}));

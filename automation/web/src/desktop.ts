export type ArtifactSelection = { sessionId: string; category: "artifacts" | "evidence"; relativePath: string };
declare global {
  interface Window {
    iotDesktop?: {
      request: (input: { url: string; method?: string; body?: string }) => Promise<unknown>;
      openArtifact: (input: ArtifactSelection & { reveal?: boolean }) => Promise<unknown>;
      previewArtifact: (input: ArtifactSelection) => Promise<unknown>;
      openDirectory: (sessionId: string) => Promise<unknown>;
      configureTool: (kind: "sdk" | "dumpcap", detectedSdk?: string) => Promise<unknown>;
      registerRoot: () => Promise<unknown>;
      chooseWorkspace: () => Promise<unknown>;
      importWorkspace: () => Promise<unknown>;
      openPreparation: (kind: "sdk" | "usb" | "capture") => Promise<unknown>;
      status: () => Promise<{ state: string; workspace: string; bundle: string }>;
    };
  }
}
export const desktop = window.iotDesktop;
export const evidenceUrl = (url: string) => desktop ? `app://evidence${url}` : url;

import { forwardRef, useImperativeHandle, useRef } from "react";
import { Excalidraw, exportToBlob } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";

export interface WhiteboardHandle {
  /** PNG data URL of the current drawing, or null if empty. */
  exportPng: () => Promise<string | null>;
  scene: () => { elements: readonly unknown[] } | null;
  clear: () => void;
}

const Whiteboard = forwardRef<WhiteboardHandle, { height?: number }>(function Whiteboard({ height = 420 }, ref) {
  const apiRef = useRef<any>(null);
  useImperativeHandle(ref, () => ({
    async exportPng() {
      const api = apiRef.current;
      if (!api) return null;
      const elements = api.getSceneElements().filter((e: any) => !e.isDeleted);
      if (!elements.length) return null;
      const blob: Blob = await exportToBlob({
        elements,
        appState: { ...api.getAppState(), exportBackground: true, viewBackgroundColor: "#ffffff" },
        files: api.getFiles(),
        mimeType: "image/png",
        maxWidthOrHeight: 1600,
      });
      return await new Promise<string>((resolve) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result));
        r.readAsDataURL(blob);
      });
    },
    scene() {
      const api = apiRef.current;
      return api ? { elements: api.getSceneElements().filter((e: any) => !e.isDeleted) } : null;
    },
    clear() {
      apiRef.current?.resetScene();
    },
  }));
  return (
    <div style={{ height }} className="overflow-hidden rounded-lg ring-1 ring-slate-200 dark:ring-slate-700">
      <Excalidraw excalidrawAPI={(api) => (apiRef.current = api)} UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false, export: false } }} />
    </div>
  );
});

export default Whiteboard;

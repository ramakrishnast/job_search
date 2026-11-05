import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";

export default function CodeEditor({ value, onChange, height = "320px" }: { value: string; onChange: (v: string) => void; height?: string }) {
  const dark = typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  return (
    <div className="overflow-hidden rounded-lg ring-1 ring-slate-200 dark:ring-slate-700">
      <CodeMirror value={value} height={height} theme={dark ? "dark" : "light"} extensions={[python()]} onChange={onChange} basicSetup={{ tabSize: 4 }} />
    </div>
  );
}

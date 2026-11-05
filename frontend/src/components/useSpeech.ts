import { useCallback, useEffect, useRef, useState } from "react";

/** Browser speech-to-text (Web Speech API; Chrome/Edge/Safari). Audio never leaves the browser's speech service. */
export function useSpeech(onFinal: (text: string) => void) {
  const Rec = typeof window !== "undefined" ? ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition) : null;
  const supported = !!Rec;
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const recRef = useRef<any>(null);
  const cb = useRef(onFinal);
  cb.current = onFinal;

  const stop = useCallback(() => {
    recRef.current?.stop();
    setListening(false);
    setInterim("");
  }, []);

  const start = useCallback(() => {
    if (!Rec) return;
    const rec = new Rec();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = navigator.language || "en-US";
    rec.onresult = (e: any) => {
      let partial = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) cb.current(r[0].transcript.trim());
        else partial += r[0].transcript;
      }
      setInterim(partial);
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => setListening(false);
    rec.start();
    recRef.current = rec;
    setListening(true);
  }, [Rec]);

  useEffect(() => () => recRef.current?.stop(), []);
  return { supported, listening, interim, start, stop };
}

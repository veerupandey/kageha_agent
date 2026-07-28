/** Browser mic → STT and text → TTS helpers for the WebUI. */

let activeAudio: HTMLAudioElement | null = null;

export function stopSpokenReply(): void {
  if (activeAudio) {
    activeAudio.pause();
    activeAudio.src = "";
    activeAudio = null;
  }
}

/** Flatten assistant markdown into speakable plain text. */
export function plainSpeakable(text: string): string {
  return String(text || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*]\([^)]*\)/g, " ")
    .replace(/\[([^\]]*)]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2")
    .replace(/^>\s?/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

export async function speakText(
  sessionId: string,
  text: string,
): Promise<void> {
  const spoken = plainSpeakable(text);
  if (!sessionId || !spoken) return;
  stopSpokenReply();
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/tts`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: spoken.slice(0, 1200) }),
    },
  );
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error || `TTS failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  activeAudio = audio;
  audio.onended = () => {
    URL.revokeObjectURL(url);
    if (activeAudio === audio) activeAudio = null;
  };
  audio.onerror = () => {
    URL.revokeObjectURL(url);
    if (activeAudio === audio) activeAudio = null;
  };
  await audio.play();
}

export async function transcribeBlob(
  sessionId: string,
  blob: Blob,
  filename = "voice.webm",
): Promise<string> {
  const form = new FormData();
  form.append("file", blob, filename);
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/stt`,
    { method: "POST", body: form },
  );
  const data = (await res.json().catch(() => ({}))) as {
    text?: string;
    error?: string;
  };
  if (!res.ok) {
    throw new Error(data.error || `STT failed (${res.status})`);
  }
  return String(data.text || "").trim();
}

/** Record until stop() is called. Returns audio blob (webm/ogg). */
export async function startMicRecording(): Promise<{
  stop: () => Promise<Blob>;
}> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Microphone not available in this browser");
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus"
    : MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : "";
  const recorder = mime
    ? new MediaRecorder(stream, { mimeType: mime })
    : new MediaRecorder(stream);
  const chunks: BlobPart[] = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data);
  };
  recorder.start();

  return {
    stop: () =>
      new Promise((resolve, reject) => {
        recorder.onerror = () => {
          stream.getTracks().forEach((t) => t.stop());
          reject(new Error("Recording failed"));
        };
        recorder.onstop = () => {
          stream.getTracks().forEach((t) => t.stop());
          const type = recorder.mimeType || "audio/webm";
          resolve(new Blob(chunks, { type }));
        };
        if (recorder.state === "recording") recorder.stop();
        else {
          stream.getTracks().forEach((t) => t.stop());
          resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
        }
      }),
  };
}

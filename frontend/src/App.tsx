import { useEffect, useState } from "react";

type Message = {
  role: "user" | "assistant";
  content: string;
};

const API_BASE = "http://127.0.0.1:8000";

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isReady, setIsReady] = useState(true);
  const [statusMessage, setStatusMessage] = useState("");

  useEffect(() => {
    let mounted = true;
    const checkStatus = async () => {
      try {
        const res = await fetch(`${API_BASE}/status`);
        if (!res.ok) {
          if (mounted) {
            setIsReady(false);
            setStatusMessage("モデルの初期化に失敗しました。");
          }
          return;
        }
        const data = await res.json();
        if (mounted) {
          const ready = Boolean(data?.ready);
          setIsReady(ready);
          setStatusMessage(ready ? "" : "モデル初期化中です...");
        }
      } catch {
        if (mounted) {
          setIsReady(false);
          setStatusMessage("バックエンドに接続できません。");
        }
      }
    };

    checkStatus();
    const timer = setInterval(checkStatus, 3000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  const updateAssistant = (chunk: string) => {
    setMessages((prev) => {
      const next = [...prev];
      const idx = next.map((m) => m.role).lastIndexOf("assistant");
      if (idx === -1) {
        next.push({ role: "assistant", content: chunk });
        return next;
      }
      next[idx] = {
        ...next[idx],
        content: next[idx].content + chunk,
      };
      return next;
    });
  };

  const sendMessage = async () => {
    if (!input.trim() || isStreaming || !isReady) return;
    const query = input.trim();

    setMessages((prev) => [...prev, { role: "user", content: query }, { role: "assistant", content: "" }]);
    setInput("");
    setIsStreaming(true);

    const res = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });

    if (!res.ok || !res.body) {
      setIsStreaming(false);
      updateAssistant("[Error] Failed to stream response.");
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const data = line.replace("data:", "").trim();
        if (data === "[DONE]") {
          setIsStreaming(false);
          return;
        }
        updateAssistant(data);
      }
    }

    setIsStreaming(false);
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-50 via-white to-slate-100 text-slate-900">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-10">
        <header className="flex flex-col gap-2">
          <h1 className="text-3xl font-semibold tracking-tight">RAG Chatbot MVP</h1>
        </header>

        <section className="flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-3">
            {messages.length === 0 && (
              <div className="text-sm text-slate-500">まだ会話がありません。最初の質問を入力してください。</div>
            )}
            {statusMessage && (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                {statusMessage}
              </div>
            )}
            {messages.map((message, idx) => (
              <div
                key={idx}
                className={`rounded-xl px-4 py-3 text-sm ${
                  message.role === "user"
                    ? "bg-slate-900 text-white"
                    : "bg-slate-100 text-slate-800"
                }`}
              >
                {message.content}
              </div>
            ))}
          </div>

          <div className="flex flex-col gap-2">
            <textarea
              className="min-h-[100px] w-full rounded-xl border border-slate-200 p-3 text-sm focus:border-slate-400 focus:outline-none"
              placeholder="ここに質問を入力してください。"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={!isReady}
            />
            <button
              className="inline-flex items-center justify-center rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
              onClick={sendMessage}
              disabled={isStreaming || !isReady}
            >
              {isStreaming ? "回答中..." : "送信"}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}

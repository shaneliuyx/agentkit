/**
 * ChatPanel — multi-turn chat interface replacing the single-task textarea.
 *
 * Each submitted message starts a run on the active session. Past turns
 * accumulate in a scrollable thread; the current run's streaming status
 * appears inline beneath the latest user message.
 */
import { useEffect, useRef, useState } from "react";
import { cancelRun, openRunStream, type RunStreamHandle } from "../../api/sse";
import { useRunStore } from "../../store/runStore";
import type { RunMode } from "../../api/types";
import "./hud.css";

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  status?: "running" | "done" | "error";
}

interface ChatPanelProps {
  sessionId: string | null;
  mode: RunMode;
  onModeChange: (mode: RunMode) => void;
}

let _msgId = 0;

export function ChatPanel({ sessionId, mode, onModeChange }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef<RunStreamHandle | null>(null);

  const status = useRunStore((s) => s.status);
  const apply = useRunStore((s) => s.apply);
  const beginRun = useRunStore((s) => s.beginRun);

  const isRunning = status === "running" || status === "connecting";
  const canSend = !!sessionId && input.trim().length > 0 && !isRunning;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Reflect final run result as an assistant message.
  useEffect(() => {
    if (status !== "done") return;
    const rs = useRunStore.getState().result;
    if (!rs) return;
    const result = rs.result;
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant" && last.status === "running") {
        const preview = result.length > 500 ? result.slice(0, 500) + "…" : result;
        return prev.map((m) =>
          m.id === last.id ? { ...m, content: preview, status: "done" } : m
        );
      }
      return prev;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const handleSend = () => {
    const req = input.trim();
    if (!sessionId || !req) return;

    const userMsg: ChatMessage = { id: ++_msgId, role: "user", content: req };
    const asstMsg: ChatMessage = { id: ++_msgId, role: "assistant", content: "", status: "running" };
    setMessages((prev) => [...prev, userMsg, asstMsg]);
    setInput("");

    streamRef.current?.close();
    beginRun(sessionId, mode);
    streamRef.current = openRunStream(sessionId, req, {
      onEvent: apply,
      onError: (message) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === asstMsg.id ? { ...m, content: message, status: "error" } : m
          )
        );
        if (useRunStore.getState().status !== "done") {
          apply({
            type: "error",
            session_id: sessionId,
            ts: Date.now() / 1000,
            payload: { message, where: "sse" },
          });
        }
      },
    });
  };

  return (
    <div className="chat-panel">
      <div className="chat-thread" aria-live="polite" aria-label="Conversation">
        {messages.length === 0 && (
          <p className="chat-empty">Describe a task to plan and run…</p>
        )}
        {messages.map((msg) => (
          <div key={msg.id} className={`chat-bubble chat-bubble--${msg.role}`}>
            {msg.role === "user" ? (
              <span>{msg.content}</span>
            ) : (
              <span className={msg.status === "error" ? "chat-error" : ""}>
                {msg.status === "running" ? (
                  <span className="chat-spinner" aria-label="Running">●●●</span>
                ) : (
                  msg.content || <em>Run complete — see result panel.</em>
                )}
              </span>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <form
        className="chat-input-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSend) handleSend();
        }}
      >
        <textarea
          className="chat-input"
          placeholder="Describe a requirement…"
          value={input}
          rows={2}
          aria-label="New message"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (canSend) handleSend();
            }
          }}
        />
        <div className="chat-controls">
          <div className="run-mode" role="group" aria-label="Planning mode">
            <button
              type="button"
              className="run-mode-btn"
              data-active={mode === "auto"}
              onClick={() => onModeChange("auto")}
              disabled={isRunning}
            >
              auto
            </button>
            <button
              type="button"
              className="run-mode-btn"
              data-active={mode === "llm"}
              onClick={() => onModeChange("llm")}
              disabled={isRunning}
            >
              llm
            </button>
          </div>
          <button type="submit" className="btn btn-primary" disabled={!canSend}>
            Send
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={() => { if (sessionId) cancelRun(sessionId).catch(() => {}); }}
            disabled={!isRunning}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}

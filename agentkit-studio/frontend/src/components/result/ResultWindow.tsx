/**
 * ResultWindow — VS Code Copilot-style right-side chat panel.
 *
 * The finished run result appears as the first assistant message in a unified
 * thread. Follow-up turns (POST /session/{id}/chat) append below it.
 * "Continue as run" composes original-task + result context + user reply and
 * signals RunBar via the pendingContinue store field to fire a new /run.
 */
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useRunStore } from "../../store/runStore";
import "./result.css";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

const API = "/api";

export function ResultWindow() {
  const status = useRunStore((s) => s.status);
  const result = useRunStore((s) => s.result);
  const sessionId = useRunStore((s) => s.sessionId);
  const task = useRunStore((s) => s.task);
  const setContinue = useRunStore((s) => s.setContinue);

  const [dismissed, setDismissed] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const threadEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Re-open + reset thread each new finished run.
  useEffect(() => {
    if (result) {
      setDismissed(false);
      setMessages([]);
    }
  }, [result]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (status !== "done" || !result || !result.result.trim() || dismissed) {
    return null;
  }

  async function sendChat() {
    const text = input.trim();
    if (!text || sending || !sessionId) return;

    const userMsg: ChatMessage = { role: "user", content: text };
    const historyBefore = [...messages];
    const next = [...historyBefore, userMsg];
    setMessages(next);
    setInput("");
    setSending(true);

    try {
      const res = await fetch(`${API}/session/${sessionId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: historyBefore }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { reply: string };
      setMessages([...next, { role: "assistant", content: data.reply }]);
    } catch (err) {
      setMessages([
        ...next,
        { role: "assistant", content: `⚠ Error: ${String(err)}` },
      ]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }

  function continueAsRun() {
    const text = input.trim();
    if (!text || !sessionId) return;
    const snippet = result!.result.slice(0, 600);
    const combined = [
      task ? `Original task: ${task}` : "",
      `Context from previous run:\n${snippet}${result!.result.length > 600 ? "…" : ""}`,
      `Follow-up: ${text}`,
    ]
      .filter(Boolean)
      .join("\n\n");
    setContinue(combined);
    setDismissed(true);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  }

  return (
    <div className="chat-panel" role="complementary" aria-label="Agent chat">
      {/* ── header ── */}
      <header className="chat-panel-head">
        <div className="chat-panel-head-left">
          <span className="chat-panel-icon">⬡</span>
          <span className="chat-panel-title mono">Agent Chat</span>
        </div>
        <button
          type="button"
          className="chat-panel-close"
          onClick={() => setDismissed(true)}
          aria-label="Close"
        >
          ×
        </button>
      </header>

      {/* ── thread ── */}
      <div className="chat-thread">
        {/* Initial result as first assistant message */}
        <div className="chat-msg chat-msg--assistant">
          <div className="chat-msg-avatar">⬡</div>
          <div className="chat-msg-body">
            <div className="chat-msg-meta mono">
              <span className="chat-msg-role">AgentKit</span>
            </div>
            {result.result_path && (
              <div className="chat-msg-path-row mono">
                <span className="chat-msg-path" title="Click to copy">
                  {result.result_path}
                </span>
                <button
                  type="button"
                  className="chat-btn-copy"
                  onClick={() => navigator.clipboard?.writeText(result.result_path)}
                  title="Copy path"
                >
                  ⎘
                </button>
                <button
                  type="button"
                  className="chat-btn-copy"
                  onClick={() => {
                    const name = result.result_path.split("/").pop() ?? "result.md";
                    const blob = new Blob([result.result], { type: "text/markdown" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = name;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                  title="Download file"
                >
                  ↓
                </button>
              </div>
            )}
            <div className="chat-msg-content markdown-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {result.result}
              </ReactMarkdown>
            </div>
          </div>
        </div>

        {/* Follow-up turns */}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`chat-msg chat-msg--${msg.role}`}
          >
            <div className="chat-msg-avatar">
              {msg.role === "assistant" ? "⬡" : "◈"}
            </div>
            <div className="chat-msg-body">
              <div className="chat-msg-meta mono">
                <span className="chat-msg-role">
                  {msg.role === "assistant" ? "AgentKit" : "You"}
                </span>
              </div>
              <div className="chat-msg-content markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {msg.content}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        ))}

        {sending && (
          <div className="chat-msg chat-msg--assistant">
            <div className="chat-msg-avatar">⬡</div>
            <div className="chat-msg-body">
              <div className="chat-msg-meta mono">
                <span className="chat-msg-role">AgentKit</span>
              </div>
              <div className="chat-msg-content chat-msg-thinking">
                <span className="chat-dot" />
                <span className="chat-dot" />
                <span className="chat-dot" />
              </div>
            </div>
          </div>
        )}

        <div ref={threadEndRef} />
      </div>

      {/* ── input ── */}
      <div className="chat-input-area">
        <textarea
          ref={inputRef}
          className="chat-input"
          rows={3}
          placeholder={
            sessionId
              ? "Ask a follow-up… (Enter to send)"
              : "No active session"
          }
          disabled={!sessionId || sending}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="chat-input-actions">
          <button
            type="button"
            className="chat-btn-continue"
            disabled={!input.trim() || !sessionId || sending}
            onClick={continueAsRun}
            title="Send as new agent run with full context"
          >
            ↻ Continue run
          </button>
          <button
            type="button"
            className="chat-btn-send"
            disabled={!input.trim() || !sessionId || sending}
            onClick={sendChat}
            title="Ask follow-up (no re-run)"
          >
            ↑
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Run controls (SPEC §6). Requirement input + auto/llm mode toggle + Run/Cancel.
 * Run opens the SSE stream against the active session; Cancel posts cooperative
 * cancel. The session must be connected (BackendPanel) before Run is enabled.
 */
import { useEffect, useRef, useState } from "react";
import { cancelRun, openRunStream, type RunStreamHandle } from "../../api/sse";
import { useRunStore } from "../../store/runStore";
import type { RunMode } from "../../api/types";
import "./config.css";

interface RunBarProps {
  sessionId: string | null;
  mode: RunMode;
  onModeChange: (mode: RunMode) => void;
}

export function RunBar({ sessionId, mode, onModeChange }: RunBarProps) {
  const [requirement, setRequirement] = useState("");
  const status = useRunStore((s) => s.status);
  const apply = useRunStore((s) => s.apply);
  const beginRun = useRunStore((s) => s.beginRun);
  const pendingContinue = useRunStore((s) => s.pendingContinue);
  const setContinue = useRunStore((s) => s.setContinue);
  const streamRef = useRef<RunStreamHandle | null>(null);

  const isRunning = status === "running" || status === "connecting";
  const canRun = !!sessionId && requirement.trim().length > 0 && !isRunning;

  // Consume one-shot "continue run" signal from ResultWindow.
  useEffect(() => {
    if (!pendingContinue || isRunning) return;
    setContinue(null);
    handleRun(pendingContinue);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingContinue]);

  const handleRun = (reqOverride?: string) => {
    const req = reqOverride ?? requirement.trim();
    if (!sessionId || !req) {
      return;
    }
    streamRef.current?.close();
    beginRun(sessionId, mode);
    streamRef.current = openRunStream(sessionId, req, {
      onEvent: apply,
      onError: (message) => {
        // Surface only if the run hasn't already completed cleanly.
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

  const handleCancel = async () => {
    if (!sessionId) {
      return;
    }
    try {
      await cancelRun(sessionId);
    } catch {
      // Cancel is best-effort; the runner will stop at the next phase boundary.
    }
  };

  return (
    <form
      className="run-bar"
      onSubmit={(e) => {
        e.preventDefault();
        if (canRun) {
          handleRun();
        }
      }}
    >
      <textarea
        className="run-input"
        placeholder="Describe a requirement to plan, deploy, and run…"
        value={requirement}
        onChange={(e) => setRequirement(e.target.value)}
        onKeyDown={(e) => {
          // Enter submits; Shift+Enter inserts a newline (multi-line requirements).
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (canRun) {
              handleRun();
            }
          }
        }}
        rows={2}
        aria-label="Requirement"
      />

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

      <button type="submit" className="btn btn-primary" disabled={!canRun}>
        Run
      </button>
      <button
        type="button"
        className="btn btn-danger"
        onClick={handleCancel}
        disabled={!isRunning}
      >
        Cancel
      </button>
    </form>
  );
}

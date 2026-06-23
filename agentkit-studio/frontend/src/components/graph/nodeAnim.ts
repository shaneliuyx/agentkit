/**
 * anime.js motion helpers (SPEC §6 "Motion"). React Flow owns layout/structure;
 * these own transitions. All motion is on transform/opacity (compositor-friendly)
 * and gated behind `prefers-reduced-motion`.
 */
import anime from "animejs";

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Staged entrance for a node activating in the time sequence: fade + lift in after
 * `delayMs` (the fan-out stagger from nodeLifecycle.entranceDelayMs), so a phase's
 * orchestrator reveals first, its spokes fan out together, and the reduce converges
 * last. Compositor-friendly (opacity + translateY only). Reduced-motion → instant.
 * Returns a cleanup fn that stops the timeline and clears the inline props.
 */
export function revealNode(el: HTMLElement, delayMs: number): () => void {
  if (prefersReducedMotion()) {
    el.style.opacity = "";
    el.style.transform = "";
    return () => undefined;
  }
  const instance = anime({
    targets: el,
    opacity: [0, 1],
    translateY: [8, 0],
    duration: 360,
    delay: delayMs,
    easing: "easeOutExpo",
  });
  return () => {
    instance.pause();
    el.style.opacity = "";
    el.style.transform = "";
  };
}

/**
 * Settle a node as it transitions active→done: a brief scale dip back to rest, so
 * the convergence (spokes → reduce) reads as "results landing". Transform-only,
 * reduced-motion → no-op. One-shot; returns a cleanup fn.
 */
export function settleNode(el: HTMLElement): () => void {
  if (prefersReducedMotion()) {
    el.style.transform = "";
    return () => undefined;
  }
  const instance = anime({
    targets: el,
    scale: [1.04, 0.98, 1],
    duration: 420,
    easing: "easeOutBack",
  });
  return () => {
    instance.pause();
    anime.set(el, { scale: 1 });
  };
}

/**
 * Pulse a running node: gentle scale + glow opacity loop. Returns a cleanup fn
 * that stops the timeline and resets the transform.
 */
export function pulseRunning(el: HTMLElement): () => void {
  if (prefersReducedMotion()) {
    el.style.setProperty("--pulse-opacity", "1");
    return () => undefined;
  }
  const instance = anime({
    targets: el,
    scale: [1, 1.04, 1],
    duration: 1400,
    easing: "easeInOutSine",
    loop: true,
  });
  return () => {
    instance.pause();
    anime.set(el, { scale: 1 });
  };
}

/**
 * Count-up tween for the token meter. Calls `onUpdate` with the interpolated
 * integer each frame; honors reduced-motion by jumping straight to `to`.
 */
export function countUp(
  from: number,
  to: number,
  onUpdate: (value: number) => void,
  duration = 600,
): () => void {
  if (prefersReducedMotion() || from === to) {
    onUpdate(to);
    return () => undefined;
  }
  const obj = { v: from };
  const instance = anime({
    targets: obj,
    v: to,
    duration,
    easing: "easeOutExpo",
    round: 1,
    update: () => onUpdate(Math.round(obj.v)),
  });
  return () => instance.pause();
}

/**
 * Flow animation on an active edge path (stroke-dashoffset). Pass the SVG <path>;
 * returns a cleanup fn.
 */
export function flowEdge(path: SVGPathElement): () => void {
  if (prefersReducedMotion()) {
    return () => undefined;
  }
  path.style.strokeDasharray = "6 6";
  const instance = anime({
    targets: path,
    strokeDashoffset: [12, 0],
    duration: 700,
    easing: "linear",
    loop: true,
  });
  return () => {
    instance.pause();
    path.style.strokeDasharray = "";
    path.style.strokeDashoffset = "";
  };
}

/**
 * Terminal (xterm.js) attached to a WebSocket that speaks the SSH console protocol (modules/ssh/backend/service.py):
 * output arrives as binary frames, status as JSON; input/resize go out as JSON. Lazy chunk via @jarvis/ui `Terminal`.
 * Colours and font come from the theme tokens, so the terminal follows every theme.
 */
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";

export interface TerminalProps {
  /** ws:// or wss:// URL including the one-time ticket. */
  url: string;
  /** Called once when the session ends (message for the user, Polish). */
  onClosed?: (message: string) => void;
  /** Extra keys row (Esc, Tab, Ctrl, arrows) — on by default on touch screens. */
  keys?: boolean;
  label?: string;
}

const css = (name: string, fallback: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

function theme() {
  const bg = css("--bg", "#0f1320");
  return {
    background: bg, foreground: css("--text", "#e8ebf7"), cursor: css("--accent", "#6c8cff"), cursorAccent: bg,
    selectionBackground: css("--accent", "#6c8cff") + "55",
  };
}

const KEYS: { label: string; seq: string; aria: string }[] = [
  { label: "Esc", seq: "\x1b", aria: "Escape" },
  { label: "Tab", seq: "\t", aria: "Tab" },
  { label: "↑", seq: "\x1b[A", aria: "Strzałka w górę" },
  { label: "↓", seq: "\x1b[B", aria: "Strzałka w dół" },
  { label: "←", seq: "\x1b[D", aria: "Strzałka w lewo" },
  { label: "→", seq: "\x1b[C", aria: "Strzałka w prawo" },
  { label: "|", seq: "|", aria: "Kreska pionowa" },
  { label: "~", seq: "~", aria: "Tylda" },
  { label: "/", seq: "/", aria: "Ukośnik" },
];

export default function TerminalView({ url, onClosed, keys, label = "Terminal" }: TerminalProps) {
  const box = useRef<HTMLDivElement>(null);
  const sock = useRef<WebSocket | null>(null);
  const term = useRef<XTerm | null>(null);
  const [ctrl, setCtrl] = useState(false);
  const ctrlRef = useRef(false);
  ctrlRef.current = ctrl;
  const closed = useRef(onClosed);
  closed.current = onClosed;
  const showKeys = keys ?? window.matchMedia?.("(pointer: coarse)").matches;

  useEffect(() => {
    const t = new XTerm({
      cursorBlink: true, fontFamily: css("--font-mono", "monospace"), fontSize: 14, scrollback: 5000,
      theme: theme(), allowProposedApi: false, convertEol: false,
    });
    const fit = new FitAddon();
    t.loadAddon(fit);
    t.open(box.current!);
    fit.fit();
    term.current = t;

    // The ticket in the URL is single-use: connect only after this render settles, so React StrictMode's
    // mount -> unmount -> mount (dev) cancels the first attempt instead of burning the ticket.
    let ws: WebSocket | null = null;
    let ended = false;
    const end = (msg: string) => {
      if (ended) return;
      ended = true;
      t.write(`\r\n\x1b[2m— ${msg} —\x1b[0m\r\n`);
      closed.current?.(msg);
    };
    const send = (o: object) => ws?.readyState === WebSocket.OPEN && ws.send(JSON.stringify(o));
    const resize = () => {
      try {
        fit.fit();
      } catch {
        /* hidden */
      }
      send({ t: "resize", c: t.cols, r: t.rows });
    };
    const timer = window.setTimeout(() => {
    ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    sock.current = ws;
    ws.onopen = () => resize();
    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        t.write(new Uint8Array(e.data));
        return;
      }
      try {
        const m = JSON.parse(e.data as string);
        if (m.t === "status") t.focus();
        else if (m.t === "error") end(m.msg);
        else if (m.t === "closed") end(m.msg);
      } catch {
        /* ignore */
      }
    };
    ws.onclose = (e) =>
      end(e.code === 4401 ? "Sesja wygasła albo bilet jest nieważny — połącz ponownie." : e.code === 4403 ? "Odmowa (zły adres pochodzenia)." : "Rozłączono.");
    ws.onerror = () => end("Nie udało się połączyć z serwerem Jarvisa.");
    }, 0);
    const data = t.onData((d) => {
      if (ctrlRef.current && d.length === 1) {
        const c = d.toUpperCase().charCodeAt(0);
        if (c >= 64 && c <= 95) d = String.fromCharCode(c - 64); // Ctrl+letter
        setCtrl(false);
      }
      send({ t: "in", d });
    });
    const ro = new ResizeObserver(() => resize());
    ro.observe(box.current!);
    // follow theme changes
    const mo = new MutationObserver(() => {
      t.options.theme = theme();
      t.options.fontFamily = css("--font-mono", "monospace");
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["style", "data-theme"] });
    return () => {
      ro.disconnect();
      mo.disconnect();
      data.dispose();
      ended = true;
      window.clearTimeout(timer);
      ws?.close();
      t.dispose();
    };
  }, [url]);

  const press = (seq: string) => {
    sock.current?.readyState === WebSocket.OPEN && sock.current.send(JSON.stringify({ t: "in", d: seq }));
    term.current?.focus();
  };

  return (
    <div className="term">
      <div className="term-screen" ref={box} role="application" aria-label={label} />
      {showKeys && (
        <div className="term-keys" role="toolbar" aria-label="Klawisze specjalne">
          <button type="button" className={`bare term-key ${ctrl ? "on" : ""}`} aria-pressed={ctrl} onClick={() => setCtrl(!ctrl)}>Ctrl</button>
          {KEYS.map((k) => (
            <button key={k.label} type="button" className="bare term-key" aria-label={k.aria} onClick={() => press(k.seq)}>{k.label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

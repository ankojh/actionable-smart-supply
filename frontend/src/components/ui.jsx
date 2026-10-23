import { useEffect, useRef } from "react";

export const money = (value) => value == null ? "TBD" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
export const words = (value = "") => String(value).toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
export const date = (value, full = false) => value ? new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00` : value).toLocaleString("en-US", full ? { dateStyle: "medium", timeStyle: "short" } : { month: "short", day: "numeric" }) : "—";
export const planTitle = (plan) => plan?.actions.map((a) => `${a.kind === "transfer" ? "Internal transfer" : a.supplier} (${a.quantity})`).join(" + ") || "Recovery plan";

export function Icon({ name, size = 20, ...props }) {
  const paths = {
    external: <><path d="M14 3h7v7M21 3 10 14M10 3H4v17h17v-6"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/></>,
    box: <><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="M3 8v9l9 5 9-5V8M12 13v9M7.5 5.5l9 5"/></>,
    inbox: <><path d="M4 5h16l2 10v5H2v-5L4 5Z"/><path d="M2 15h6l2 3h4l2-3h6M8 9h8"/></>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/></>,
    activity: <path d="M3 12h4l3-8 4 16 3-8h4"/>,
    chart: <><path d="M4 3v17h17M8 15v-4M13 15V7M18 15v-6"/></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
    back: <path d="M20 12H4m6-6-6 6 6 6"/>,
    chevron: <path d="m9 5 7 7-7 7"/>,
    check: <path d="m5 12 4 4L19 6"/>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/></>,
    alert: <><path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3h.01"/></>,
    close: <path d="m6 6 12 12M6 18 18 6"/>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 6.1a8 8 0 0 1 13 2M4.9 15.9a8 8 0 0 0 13 2"/></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
    users: <><circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m3 10v-3a6 6 0 0 0-3-5"/></>,
    snow: <><path d="M12 2v20M3.4 7l17.2 10M3.4 17 20.6 7M9 4l3 3 3-3M9 20l3-3 3 3"/></>,
    search: <><circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M9 21h6"/></>,
    brain: <><path d="M12 5a3 3 0 0 0-6-1 4 4 0 0 0-3 6 4 4 0 0 0 1 7 4 4 0 0 0 8 2V5ZM12 5a3 3 0 0 1 6-1 4 4 0 0 1 3 6 4 4 0 0 1-1 7 4 4 0 0 1-8 2M6 8l3 2M4 14h4M18 8l-3 2M20 14h-4M8 18l1-3M16 18l-1-3"/></>,
    spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/></>,
    send: <><path d="m22 2-7 20-4-9-9-4 20-7ZM11 13 22 2"/></>,
    plus: <path d="M12 5v14M5 12h14"/>,
    download: <><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></>,
    gear: <><path d="m9 3-1 3-3 1 1 3-2 2 2 2-1 3 3 1 1 3h6l1-3 3-1-1-3 2-2-2-2 1-3-3-1-1-3H9Z"/><circle cx="12" cy="12" r="4"/></>,
    compare: <><path d="M3 5h7v14H3zM14 5h7v14h-7zM6 9h1M6 13h1M17 9h1M17 13h1"/></>,
    truck: <><path d="M2 5h12v12H2zM14 9h4l4 4v4h-8M18 9v4h4"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name] || paths.box}</svg>;
}

export function Badge({ children, tone = "neutral", dot = false }) { return <span className={`badge ${tone}`}>{dot && <i/>}{children}</span>; }
export function Button({ children, variant = "secondary", className = "", ...props }) { return <button className={`button ${variant} ${className}`} {...props}>{children}</button>; }
export function Empty({ title, children, icon = "file" }) { return <div className="empty"><span className="empty-icon"><Icon name={icon} size={25}/></span><h3>{title}</h3><p>{children}</p></div>; }
export function SectionHeading({ title, subtitle, children }) { return <div className="section-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{children}</div>; }
export function Metric({ label, value, note, icon, tone = "blue" }) { return <div className={`metric ${tone}`}><div className="metric-top"><span>{label}</span><Icon name={icon}/></div><strong>{value ?? "—"}</strong><small>{note}</small></div>; }

export function Modal({ title, children, onClose, wide = false }) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    const focusable = () => [...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')];
    (focusable()[0] || dialog).focus();
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const keydown = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const elements = focusable();
        const first = elements[0]; const last = elements.at(-1);
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    };
    dialog.addEventListener("keydown", keydown);
    return () => { dialog.removeEventListener("keydown", keydown); document.body.style.overflow = oldOverflow; previous?.focus(); };
  }, [onClose]);
  return <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}><section className={`modal ${wide ? "wide" : ""}`} ref={ref} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}><div className="modal-title"><h2>{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose}><Icon name="close"/></button></div>{children}</section></div>;
}

export function EventList({ events = [] }) {
  if (!events.length) return <Empty title="Investigation is starting" icon="activity">Specialist findings will appear here as the assessment progresses.</Empty>;
  return <div className="event-list">{events.map((event, i) => <div className="event" key={event.id || i}><span className="event-icon"><Icon name={event.role === "engine" ? "chart" : event.role === "qualification" ? "shield" : "activity"} size={16}/></span><div><strong>{words(event.role)} <span>{words(event.kind)}</span></strong><p>{event.message}</p><small>{date(event.at, true)}</small></div></div>)}</div>;
}

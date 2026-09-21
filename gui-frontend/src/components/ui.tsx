/** Small shared building blocks. */
import { useEffect, useRef, type ReactNode } from "react";

export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" aria-pressed={option.value === value} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function ErrorNote({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="note fault" role="alert">
      {children}{" "}
      {onRetry && (
        <button type="button" className="btn btn-quiet" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

/**
 * A modal that says specifically what is about to happen. Uses the browser's own <dialog>, so
 * focus is trapped, Escape closes it, and focus returns to where it was.
 */
export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
  children,
}: {
  open: boolean;
  title: string;
  confirmLabel: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog ref={ref} className="modal" aria-labelledby="confirm-title" onClose={onCancel}>
      <div className="modal-body">
        <h2 id="confirm-title">{title}</h2>
        {children}
      </div>
      <div className="modal-actions">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className={`btn ${destructive ? "btn-danger solid" : "btn-primary"}`} onClick={onConfirm} disabled={busy}>
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}

export function PageHead({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p className="sub">{sub}</p>}
      </div>
      {actions}
    </div>
  );
}

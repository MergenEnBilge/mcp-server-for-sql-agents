import { plural } from "../../components/format";

/**
 * The bar under a grid that says whether the screen matches the database. Amber while there are
 * unsaved changes, plain when there aren't. It is the only way changes are saved.
 */
export function StagedBar({
  count,
  summary,
  busy,
  saved,
  error,
  onSave,
  onDiscard,
}: {
  count: number;
  summary: string;
  busy: boolean;
  saved: string | null;
  error: string | null;
  onSave: () => void;
  onDiscard: () => void;
}) {
  if (error) {
    return (
      <div className="staged" role="alert" style={{ borderColor: "var(--fault)", background: "var(--fault-tint)" }}>
        <div className="grow">
          <strong>Not saved.</strong> {error}
        </div>
        <button type="button" className="btn" onClick={onDiscard}>
          Discard changes
        </button>
        <button type="button" className="btn btn-primary" onClick={onSave} disabled={busy}>
          Try again
        </button>
      </div>
    );
  }

  if (count === 0) {
    return (
      <div className="staged calm" role="status" aria-live="polite">
        <div className="grow">{saved ?? "No unsaved changes. What you see is what is in effect."}</div>
      </div>
    );
  }

  return (
    <div className="staged" role="status" aria-live="polite">
      <div className="grow">
        <strong>{plural(count, "unsaved change")}.</strong> {summary}
      </div>
      <button type="button" className="btn" onClick={onDiscard} disabled={busy}>
        Discard
      </button>
      <button type="button" className="btn btn-primary" onClick={onSave} disabled={busy}>
        {busy ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

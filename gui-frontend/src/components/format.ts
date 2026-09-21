const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n: number) => String(n).padStart(2, "0");

/** "21 Sep 14:02:11" in the viewer's time zone. Precise to the second, because this is evidence. */
export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** "2026-09-21 14:02:11.482 +02:00", the unambiguous form, for detail views. */
export function formatFull(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const offset = -d.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, "0")} ` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  );
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;
}

/** "3 pending changes" style helper for counts with a noun. */
export function count(n: number, noun: string): string {
  return plural(n, noun);
}

/** Days to a `YYYY-MM-DD` string in local time. */
export function toDateInput(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** The start of the next day, for an inclusive "to" date filter. */
export function endOfDayExclusive(dateInput: string): string {
  const [y, m, d] = dateInput.split("-").map(Number);
  return new Date(y ?? 1970, (m ?? 1) - 1, (d ?? 1) + 1).toISOString();
}

export function startOfDay(dateInput: string): string {
  const [y, m, d] = dateInput.split("-").map(Number);
  return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1).toISOString();
}

/** Who a subject is, for people: the display name if we have one, else the raw id. */
export function subjectLabel(subject: { subject_id: string; display_name: string | null }): string {
  return subject.display_name ?? subject.subject_id;
}

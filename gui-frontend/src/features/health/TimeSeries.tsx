import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { axisTicks, nearestIndex, niceCeil, runs, wholeCeil } from "./chartMath";

export interface SeriesDef {
  label: string;
  /** Which colour token draws it: `ink` (the subject), `quiet` (context), `fault` (failures). */
  tone: "ink" | "quiet" | "fault";
  values: (number | null)[];
}

const HEIGHT = 176;
const MARGIN = { left: 44, right: 64, top: 8, bottom: 24 };
const GAP = 2; // the surface-coloured gap between touching marks

/** The chart's width follows its container, so it stays legible in a narrow window. */
function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => entry && setWidth(Math.max(280, Math.floor(entry.contentRect.width))));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/**
 * A time series drawn as a 2px line (one series) or stacked columns (parts of a whole). One axis,
 * hairline gridlines, and the last value labelled directly. Pointing at it, or arrowing through it
 * from the keyboard, shows every series at that moment; "Show as table" gives the same numbers
 * without any pointing at all.
 */
export function TimeSeries({
  title,
  note,
  kind,
  times,
  series,
  format,
  timeLabel,
  whole = false,
  dimmed = false,
}: {
  title: string;
  note?: string;
  kind: "line" | "columns";
  times: string[];
  series: SeriesDef[];
  format: (value: number) => string;
  timeLabel: (iso: string) => string;
  /** The values are counts, so gridlines fall on whole numbers. */
  whole?: boolean;
  dimmed?: boolean;
}) {
  const [wrapRef, width] = useWidth();
  const [active, setActive] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);

  const n = times.length;
  const innerW = width - MARGIN.left - MARGIN.right;
  const innerH = HEIGHT - MARGIN.top - MARGIN.bottom;
  const totals = times.map((_, i) => series.reduce((sum, s) => sum + (s.values[i] ?? 0), 0));
  const peak = kind === "columns" ? Math.max(0, ...totals) : Math.max(0, ...series.flatMap((s) => s.values.filter((v): v is number => v !== null)));
  const top = whole ? wholeCeil(peak) : niceCeil(peak);
  const x = (i: number) => MARGIN.left + ((i + 0.5) * innerW) / Math.max(1, n);
  const y = (v: number) => MARGIN.top + innerH * (1 - v / top);
  const slot = innerW / Math.max(1, n);
  const columnWidth = Math.max(2, Math.min(24, slot - GAP));
  const empty = peak === 0;

  function onPointerMove(event: PointerEvent<SVGRectElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    setActive(nearestIndex(event.clientX - box.left, box.width, n));
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    const current = active ?? n - 1;
    const next = { ArrowLeft: current - 1, ArrowRight: current + 1, Home: 0, End: n - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    setActive(Math.min(n - 1, Math.max(0, next)));
  }

  const lastPoint = kind === "line" ? [...runs(series[0]?.values ?? [])].flat().at(-1) : undefined;

  return (
    <figure className={`chart${dimmed ? " dimmed" : ""}`}>
      <figcaption>
        <span className="chart-title">{title}</span>
        {note && <span className="muted"> {note}</span>}
        {series.length > 1 && (
          <span className="legend" aria-label="Legend">
            {[...series].reverse().map((s) => (
              <span key={s.label}>
                <svg width="16" height="8" aria-hidden="true">
                  <line x1="0" x2="16" y1="4" y2="4" className={`tone-${s.tone}`} strokeWidth="3" />
                </svg>
                {s.label}
              </span>
            ))}
          </span>
        )}
        <button type="button" className="btn btn-quiet" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          {asTable ? "Show chart" : "Show as table"}
        </button>
      </figcaption>

      {asTable ? (
        <div className="table-wrap" style={{ maxHeight: 260, overflow: "auto" }}>
          <table className="data" aria-label={`${title}, as a table`}>
            <thead>
              <tr>
                <th scope="col">Time</th>
                {series.map((s) => (
                  <th key={s.label} scope="col" className="num">
                    {s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {times.map((t, i) => (
                <tr key={t}>
                  <th scope="row">{timeLabel(t)}</th>
                  {series.map((s) => (
                    <td key={s.label} className="num">
                      {s.values[i] === null || s.values[i] === undefined ? "–" : format(s.values[i] as number)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart-plot" ref={wrapRef}>
          <svg
            width={width}
            height={HEIGHT}
            role="group"
            aria-label={`${title}. Use the left and right arrow keys to read each point.`}
            tabIndex={0}
            onKeyDown={onKeyDown}
            onFocus={() => setActive((a) => a ?? n - 1)}
            onBlur={() => setActive(null)}
          >
            {axisTicks(top).map((tick) => (
              <g key={tick}>
                <line x1={MARGIN.left} x2={width - MARGIN.right} y1={y(tick)} y2={y(tick)} className="grid-line" />
                <text x={MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" className="axis-text">
                  {format(tick)}
                </text>
              </g>
            ))}
            {[0, Math.floor((n - 1) / 2), n - 1].map((i, k) =>
              times[i] ? (
                <text key={k} x={x(i)} y={HEIGHT - 6} textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"} className="axis-text">
                  {timeLabel(times[i] as string)}
                </text>
              ) : null,
            )}

            {kind === "columns" &&
              times.map((_, i) => {
                let floor = MARGIN.top + innerH; // the baseline, then the top of what is stacked so far
                return series.map((s) => {
                  const value = s.values[i] ?? 0;
                  if (value <= 0) return null;
                  const height = Math.max(2, y(0) - y(value));
                  const bottom = floor;
                  floor -= height + GAP;
                  return (
                    <rect key={`${i}-${s.label}`} x={x(i) - columnWidth / 2} y={bottom - height} width={columnWidth} height={height} rx={2} className={`tone-${s.tone}`} />
                  );
                });
              })}

            {kind === "line" &&
              series.map((s) =>
                runs(s.values).map((run) =>
                  run.length === 1 ? (
                    <circle key={`${s.label}-${run[0]!.index}`} cx={x(run[0]!.index)} cy={y(run[0]!.value)} r={3} className={`tone-${s.tone}`} />
                  ) : (
                    <polyline
                      key={`${s.label}-${run[0]!.index}`}
                      points={run.map((p) => `${x(p.index)},${y(p.value)}`).join(" ")}
                      fill="none"
                      strokeWidth={2}
                      strokeLinejoin="round"
                      strokeLinecap="round"
                      className={`tone-${s.tone}`}
                    />
                  ),
                ),
              )}
            {lastPoint && (
              <>
                <circle cx={x(lastPoint.index)} cy={y(lastPoint.value)} r={5} className="ring tone-ink" />
                <text x={x(lastPoint.index) + 10} y={y(lastPoint.value)} dy="0.32em" className="end-label">
                  {format(lastPoint.value)}
                </text>
              </>
            )}

            {empty && (
              <text x={MARGIN.left + innerW / 2} y={MARGIN.top + innerH / 2} textAnchor="middle" className="axis-text">
                Nothing in this period
              </text>
            )}

            {active !== null && <line x1={x(active)} x2={x(active)} y1={MARGIN.top} y2={MARGIN.top + innerH} className="crosshair" />}
            <rect x={MARGIN.left} y={MARGIN.top} width={innerW} height={innerH} fill="transparent" onPointerMove={onPointerMove} onPointerLeave={() => setActive(null)} />
          </svg>

          {active !== null && times[active] && (
            <div className="tooltip" role="status" style={{ left: x(active), ...(x(active) > width * 0.6 ? { transform: "translateX(calc(-100% - 12px))" } : { transform: "translateX(12px)" }) }}>
              <div className="muted">{timeLabel(times[active] as string)}</div>
              {[...series].reverse().map((s) => (
                <div key={s.label} className="tip-row">
                  <svg width="12" height="8" aria-hidden="true">
                    <line x1="0" x2="12" y1="4" y2="4" className={`tone-${s.tone}`} strokeWidth="3" />
                  </svg>
                  <strong>{s.values[active] === null || s.values[active] === undefined ? "–" : format(s.values[active] as number)}</strong>
                  <span className="muted">{s.label}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}

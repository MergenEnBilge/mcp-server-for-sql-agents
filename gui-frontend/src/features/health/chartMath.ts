/** Numbers for drawing the health charts. Kept free of React so they can be tested on their own. */

/** The smallest "round" number (1, 2, 5, 10, 20, 50 ...) that is at least `max`. */
export function niceCeil(max: number): number {
  if (!(max > 0)) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  for (const step of [1, 2, 5, 10]) {
    if (step * magnitude >= max) return step * magnitude;
  }
  return 10 * magnitude;
}

/** Gridline values for an axis from 0 to `top`: 0, half, top. */
export function axisTicks(top: number): number[] {
  return top === 1 ? [0, 1] : [0, top / 2, top];
}

/** The index of the point nearest to horizontal position `x`, given `count` evenly spaced points
 * across `width`. Readers aim at a moment in time, not at a 2px line. */
export function nearestIndex(x: number, width: number, count: number): number {
  if (count <= 1 || width <= 0) return 0;
  const slot = width / count;
  return Math.min(count - 1, Math.max(0, Math.floor(x / slot)));
}

/** Split values into runs without gaps, so a missing reading breaks the line instead of being drawn as zero. */
export function runs(values: (number | null)[]): { index: number; value: number }[][] {
  const out: { index: number; value: number }[][] = [];
  let current: { index: number; value: number }[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length) out.push(current);
      current = [];
    } else {
      current.push({ index, value });
    }
  });
  if (current.length) out.push(current);
  return out;
}

export function formatMs(ms: number | null): string {
  if (ms === null) return "–";
  return ms >= 10_000 ? `${(ms / 1000).toFixed(1)} s` : `${ms.toLocaleString("en-GB")} ms`;
}

/** "3.2%", or "0%" when exactly none, or "<0.1%" rather than a misleading zero. */
export function formatRate(rate: number | null): string {
  if (rate === null) return "–";
  if (rate === 0) return "0%";
  const percent = rate * 100;
  return percent < 0.1 ? "<0.1%" : `${percent >= 10 ? percent.toFixed(0) : percent.toFixed(1)}%`;
}

/** Presentation-only scale; the result arrays and engineering values stay unchanged. */
export function suggestedDeformation(points: number[][], displacements?: number[][]): number {
  if (!points.length || !displacements?.length) return 1;
  const low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
  for (const p of points) for (let a = 0; a < 3; a++) {
    low[a] = Math.min(low[a], p[a]); high[a] = Math.max(high[a], p[a]);
  }
  let maximum = 0;
  for (const u of displacements) if (u.every(Number.isFinite)) maximum = Math.max(maximum, Math.hypot(...u));
  if (!maximum) return 1;
  const span = Math.hypot(...high.map((v, a) => v - low[a]));
  return Math.max(1, Math.min(1000, Math.round(span * .12 / maximum)));
}

/** 0 -> full static displacement -> 0, without negative or exaggerated load reversal. */
export function deformationCycle(elapsedSeconds: number): number {
  return (1 - Math.cos(Math.max(0, elapsedSeconds) * Math.PI / 2)) / 2;
}

export function updateDeformedPositions(target: Float32Array, rest: Float32Array, displacement: Float32Array, factor: number) {
  for (let i = 0; i < target.length; i++) target[i] = rest[i] + displacement[i] * factor;
}

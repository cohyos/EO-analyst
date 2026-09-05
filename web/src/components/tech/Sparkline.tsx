/** A12: a tiny inline SVG sparkline (no charting library) for a subdomain's per-week item
 * counts over the trailing 4 weeks -- most-recent week last. */
export function Sparkline({ values, width = 72, height = 20 }: { values: number[]; width?: number; height?: number }) {
  if (!values.length) {
    return <span className="text-xs text-fg-dim">—</span>;
  }
  const max = Math.max(1, ...values);
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  const points = values
    .map((v, i) => {
      const x = values.length > 1 ? i * step : width / 2;
      const y = height - (v / max) * (height - 2) - 1;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`מגמה: ${values.join(", ")}`}
      className="text-accent"
    >
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}

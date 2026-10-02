export function Skeleton({ width = "100%", height = 14, style }) {
  return <div className="skeleton" style={{ width, height, ...style }} aria-hidden="true" />;
}

export function DecisionSkeleton() {
  return (
    <div aria-busy="true" aria-label="Evaluating">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Skeleton width={150} height={40} style={{ borderRadius: 999 }} />
        <Skeleton width={180} height={14} />
      </div>
      <div className="tiles">
        {[0, 1, 2].map((i) => <Skeleton key={i} height={92} style={{ borderRadius: 12 }} />)}
      </div>
      {Array.from({ length: 8 }, (_, i) => (
        <Skeleton key={i} height={18} width={`${92 - (i % 3) * 14}%`} style={{ margin: "14px 0" }} />
      ))}
    </div>
  );
}

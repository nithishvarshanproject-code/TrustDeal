import { CircleCheck, CircleMinus, CircleX, TriangleAlert } from "lucide-react";

const STATUS = {
  pass: { icon: CircleCheck, label: "pass" },
  fail: { icon: CircleX, label: "fail" },
  warn: { icon: TriangleAlert, label: "warning" },
  skip: { icon: CircleMinus, label: "skipped" },
};

function formatValue(v) {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v) && v[0] === "stv") return `stv ${v[1].toFixed(2)} ${v[2].toFixed(2)}`;
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100);
  return String(v);
}

/** The MeTTa trail, replayed line by line (150 ms apart). Text is exactly what MeTTa returned. */
export default function AuditTrail({ trail, replayKey }) {
  return (
    <ol className="trail" key={replayKey} aria-label="Audit trail">
      {trail.map((e, i) => {
        const bonusNotApplied = (e.rule_id === "R3" || e.rule_id === "R4") && e.status === "fail";
        const { icon: Icon, label } = bonusNotApplied
          ? { ...STATUS.skip, label: "not applied" }
          : STATUS[e.status] ?? STATUS.skip;
        const iconStatus = bonusNotApplied ? "skip" : e.status;
        return (
          <li
            key={i}
            className={`trail-line${e.rule_id === "DECISION" ? " is-decision" : ""}`}
            style={{ animationDelay: `${i * 150}ms` }}
          >
            <span className="trail-n">{i + 1}</span>
            <Icon size={17} className={`st-${iconStatus}`} aria-label={label} />
            <span className="rule-chip">{e.rule_id}</span>
            <span className="trail-check">{e.check}</span>
            <span className="trail-value">{formatValue(e.value)}</span>
          </li>
        );
      })}
    </ol>
  );
}

import { Fragment } from "react";
import { Brain, Database, FileText, Gavel, ScrollText, ShieldCheck } from "lucide-react";

export const PIPELINE = [
  { name: "Input", icon: FileText },
  { name: "Seller data", icon: Database },
  { name: "MeTTa rules", icon: Brain },
  { name: "Trust", icon: ShieldCheck },
  { name: "Decision", icon: Gavel },
  { name: "Explanation", icon: ScrollText },
];

/** stage: -1 idle, 0..5 = that step is active, 6 = all done. */
export default function PipelineStrip({ stage }) {
  return (
    <div className="card pipeline" role="status" aria-label="Decision pipeline">
      {PIPELINE.map(({ name, icon: Icon }, i) => {
        const state = stage > i ? "done" : stage === i ? "active" : "";
        return (
          <Fragment key={name}>
            {i > 0 && <div className={`connector${stage >= i ? " done" : ""}`} />}
            <div className={`step ${state}`}>
              <div className="step-icon"><Icon size={17} aria-hidden="true" /></div>
              <span className="step-name">{name}</span>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

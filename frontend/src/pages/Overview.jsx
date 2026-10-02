import { useEffect, useState } from "react";
import { Activity, Info, Users } from "lucide-react";
import { api } from "../api.js";
import EmptyState from "../components/EmptyState.jsx";
import { Skeleton } from "../components/Skeleton.jsx";
import { formatINR } from "../lib/format.js";

const CHANNELS = [
  ["deal_check", "Deal check"],
  ["web_chat", "Web chat"],
  ["telegram", "Telegram"],
];
const OUTCOMES = ["APPROVE", "COUNTER", "REJECT", "ESCALATE"];

function MetricCard({ title, tooltip, children, className = "" }) {
  return (
    <article className={`card overview-card ${className}`}>
      <div className="overview-card-head">
        <h2 className="label">{title}</h2>
        <button className="overview-info" type="button" title={tooltip} aria-label={`${title}: ${tooltip}`}>
          <Info size={15} aria-hidden="true" />
        </button>
      </div>
      {children}
    </article>
  );
}

function Amount({ value }) {
  return <strong className="overview-value">{formatINR(value ?? 0)}</strong>;
}

function CountPair({ label, value }) {
  return <div className="overview-row"><span>{label}</span><b>{value}</b></div>;
}

export default function Overview() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    load();
  }, []);

  function load() {
    setError(null);
    setStats(null);
    api.overviewStats().then(setStats).catch((err) => setError(err.message));
  }

  if (error) return <div className="page"><div className="error" role="alert">{error} <button className="btn btn-sm" onClick={load}>Retry</button></div></div>;
  if (!stats) {
    return <div className="page" aria-busy="true">
      <header className="page-head"><div><h1 className="page-title">Overview</h1></div></header>
      <div className="overview-grid">{Array.from({ length: 11 }, (_, i) =>
        <Skeleton key={i} height={150} style={{ borderRadius: 14 }} />)}</div>
    </div>;
  }

  const d = stats.discounts;
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-sub">Business impact across Deal check and customer conversations.</p>
        </div>
      </header>

      {stats.deals_handled.total === 0 && (
        <div className="card overview-empty">
          <EmptyState icon={Activity} title="No business activity yet">
            Overview metrics will fill in when the agent handles its first deal.
          </EmptyState>
        </div>
      )}

      <div className="overview-grid">
        <MetricCard title="Deals handled"
          tooltip="Count each stored Deal check decision once and each customer agent request once. Channel uses the request's stored channel; Deal check counts seller decisions.">
          <strong className="overview-value">{stats.deals_handled.total}</strong>
          {CHANNELS.map(([key, label]) => <CountPair key={key} label={label} value={stats.deals_handled.by_channel[key]} />)}
        </MetricCard>

        <MetricCard title="Results"
          tooltip="Count the latest Deal check decision per deal and the latest customer agent decision per request. A manager-approved or manager-rejected escalation is counted as APPROVE or REJECT.">
          {OUTCOMES.map((result) => <CountPair key={result} label={result} value={stats.results[result]} />)}
        </MetricCard>

        <MetricCard title="Discount requested vs offered"
          tooltip="For each decided deal, requested value = list price × quantity × requested percent ÷ 100. APPROVE offers the requested percent; COUNTER uses its stored counter percent; REJECT offers ₹0. A manager-approved escalation uses its stored approved percent (or the requested percent for a Deal check override to APPROVE); a rejection offers ₹0. Pending ESCALATE decisions and counters without a stored amount are excluded from all ₹ totals.">
          <div className="overview-money-pair"><div><span>Requested</span><Amount value={d.requested} /></div>
            <div><span>Offered</span><Amount value={d.offered} /></div></div>
          <CountPair label={`${d.pending_escalations_excluded} pending escalations — excluded`} value="" />
          <CountPair label={`${d.overridden_counters_without_amount_excluded} overridden counters without amount — excluded`} value="" />
          {d.counters_without_amount_excluded > d.overridden_counters_without_amount_excluded &&
            <CountPair label={`${d.counters_without_amount_excluded - d.overridden_counters_without_amount_excluded} other counters without amount — excluded`} value="" />}
        </MetricCard>

        <MetricCard title="Discount avoided"
          tooltip="Requested value minus offered value, summed over decided deals with a known offered amount. Pending escalations and counters without a stored amount are excluded. Each value uses list price × quantity × percent ÷ 100.">
          <Amount value={d.avoided} />
          <div className="overview-caption">{d.decided_deals} decided deals with known amounts</div>
        </MetricCard>

        <MetricCard title="Discount actually given on orders"
          tooltip="Sum the stored savings amounts for customer quotes whose status is ordered. This uses quote savings saved when MeTTa created each quote.">
          <Amount value={d.discount_actually_given_on_orders} />
        </MetricCard>

        <MetricCard title="Below-cost requests blocked"
          tooltip="Count latest decisions whose result is REJECT and whose stored audit trail records R1 as fail. Each Deal check deal and each customer request is counted once.">
          <strong className="overview-value">{stats.below_cost_blocked}</strong>
        </MetricCard>

        <MetricCard title="Escalations"
          tooltip="Count Deal check decisions originally returned as ESCALATE plus customer-agent escalation tasks. Deal check escalations are resolved by a later non-ESCALATE override; customer tasks are resolved when their stored status is done.">
          <strong className="overview-value">{stats.escalations.total}</strong>
          <CountPair label="Resolved" value={stats.escalations.resolved} />
          <CountPair label="Pending" value={stats.escalations.pending} />
        </MetricCard>

        <MetricCard title="Verifications"
          tooltip="Count stored customer-agent tasks whose kind is verification. Resolved means task status is done; pending means status is open.">
          <strong className="overview-value">{stats.verifications.total}</strong>
          <CountPair label="Resolved" value={stats.verifications.resolved} />
          <CountPair label="Pending" value={stats.verifications.pending} />
        </MetricCard>

        <MetricCard title="Quote → order conversion"
          tooltip="Ordered customer quotes divided by all stored customer quotes. The rate is zero when there are no quotes.">
          <strong className="overview-value">{(stats.quotes.conversion_rate * 100).toFixed(1)}%</strong>
          <div className="overview-caption">{stats.quotes.ordered} orders from {stats.quotes.total} quotes</div>
        </MetricCard>

        <MetricCard title="Average decision confidence"
          tooltip="Arithmetic mean of non-null confidence values from the latest seller Decision per Deal check deal and latest AgentDecision per customer request.">
          <strong className="overview-value">{stats.average_confidence == null ? "—" : `${(stats.average_confidence * 100).toFixed(1)}%`}</strong>
        </MetricCard>

        <MetricCard title="Top customers by trust"
          tooltip="Trust strength and confidence come from MeTTa history-stv. Rank customers by strength × confidence, highest first; ties use customer record order. Both stored MeTTa values are shown.">
          {stats.top_customers_by_trust.length ? <ol className="overview-customers">
            {stats.top_customers_by_trust.map((customer, index) => (
              <li key={customer.customer_id}>
                <span><b>{index + 1}.</b> {customer.name}</span>
                <span className="mono">{customer.strength.toFixed(2)} / {customer.confidence.toFixed(2)}</span>
              </li>
            ))}
          </ol> : <EmptyState icon={Users} title="No customers yet">Customer trust appears after customer records exist.</EmptyState>}
        </MetricCard>
      </div>
    </div>
  );
}

export default function EmptyState({ icon: Icon, title, children }) {
  return (
    <div className="empty">
      <div className="empty-icon"><Icon size={24} aria-hidden="true" /></div>
      <h4>{title}</h4>
      {children && <p>{children}</p>}
    </div>
  );
}

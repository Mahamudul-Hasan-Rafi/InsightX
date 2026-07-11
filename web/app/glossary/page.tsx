import Icon from '@/app/component/Icon';

export default function GlossaryPage() {
  return (
    <div className="page-inner fade-up">
      <div className="between" style={{ marginBottom: 8 }}>
        <div>
          <div className="eyebrow">Shared vocabulary</div>
          <h1 className="section-title" style={{ marginTop: 4 }}>Glossary</h1>
        </div>
        <button className="btn btn-primary">
          <Icon name="plus" size={15} /> Add keyword
        </button>
      </div>
      <p className="muted" style={{ margin: '0 0 24px', fontSize: 14, maxWidth: 620 }}>
        Define how InsightX should interpret banking terms when they appear in a question.
        These rules keep generated insights consistent across every officer.
      </p>

      <div className="empty-state">
        <div className="es-ic">
          <Icon name="glossary" size={22} />
        </div>
        <h3>No keywords yet</h3>
        <p>Add your first keyword to control how InsightX interprets banking terms and ensures consistent insights across all officers.</p>
      </div>
    </div>
  );
}

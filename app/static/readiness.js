(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const count = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value).toLocaleString() : 'Unknown';
  function markup(readiness) {
    const correction = readiness?.correction || {};
    const search = readiness?.search || {};
    const evidence = readiness?.evidence || {};
    const correctionLabel = correction.ready === true ? 'Current' : 'Needs attention';
    const searchLabel = search.hybrid_ready === true ? 'Hybrid search available' : search.lexical_ready === true ? 'Text search available' : 'Search not ready';
    const evidenceLabels = {not_scanned:'Not scanned', legacy:'Migration needed', invalid:'Ledger needs inspection', stale:'Detection needs refresh', pending:'Validation pending', incomplete:'Some manuals need attention', no_candidates:'No candidates detected', tracked_candidates_validated:'Tracked candidates validated', tracked_candidates_resolved:'Tracked candidates resolved'};
    const evidenceLabel = evidenceLabels[evidence.status] || 'Coverage unknown';
    const correctionDetail = correction.ready === true ? 'Required processing and blocking reviews are current.' : correction.reason === 'testing_bypass' ? 'Testing bypass is enabled; corrections are not certified ready.' : correction.reason === 'review_pending' ? `${count(correction.blocking_reviews)} blocking review item(s).` : 'Required verification or correction outputs are not current.';
    const evidenceDetail = evidence.detected == null ? 'Detection has not established candidate counts.' : `${count(evidence.validated)} validated / ${count(evidence.detected)} detected candidates. ${count(evidence.visual_parse_pending)} need visual parsing.`;
    const contextDetail = evidence.technical_notes != null && evidence.context_link_candidates != null ? ` ${count(evidence.technical_notes)} technical notes; ${count(evidence.context_link_candidates)} candidate context links.` : '';
    const card = (title, label, detail, ready) => `<article class="v5-readiness-card ${ready ? 'current' : 'attention'}"><h3>${esc(title)}</h3><strong>${esc(label)}</strong><p>${esc(detail)}</p></article>`;
    return card('Correction readiness', correctionLabel, correctionDetail, correction.ready === true)
      + card('Search readiness', searchLabel, 'Search availability does not establish that every diagram or technical fact was extracted.', search.lexical_ready === true)
      + card('Evidence coverage', evidenceLabel, evidenceDetail + contextDetail + ' Whole-manual extraction coverage has not been measured.', false);
  }
  function render(element, readiness) {
    if (element) element.innerHTML = markup(readiness);
  }
  window.EvidenceReadiness = {markup, render};
})();

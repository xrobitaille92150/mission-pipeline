// Parser score — extrait JSON de la réponse Claude
const upstream = $('Prépa prompt scoring').item.json;
const txt = ($json && $json.content && $json.content[0] && $json.content[0].text) || '';

let score = null, signaux = [], justification = '';
try {
  const m = txt.match(/\{[\s\S]*\}/);
  if (m) {
    const o = JSON.parse(m[0]);
    const raw = typeof o.score === 'number' ? o.score : parseInt(o.score, 10);
    score = isNaN(raw) ? null : Math.min(100, Math.max(0, raw));
    signaux = Array.isArray(o.signaux) ? o.signaux : [];
    justification = o.justification || '';
  }
} catch (e) { /* score reste null, sera ignoré par Écrire notes */ }

return { json: {
  id: upstream.id,
  noteRole: upstream.noteRole,
  noteCriteres: upstream.noteCriteres,
  modeFromNotes: upstream.modeFromNotes || '',
  modeFromJd: upstream.modeFromJd || '',
  // Préparer dossier = règle Score >= 35 (remplace l ancien pré-classement grossier, 27/06/2026).
  // score null (scoring échoué/crédits) -> pas de préparation auto (fail-safe).
  preparer: (score !== null && score >= 35),
  postule: (score !== null && score >= 35 && !!upstream.easyApply),
  easyApply: !!upstream.easyApply,
  score,
  scoreSignaux: signaux.join(' | '),
  scoreJustification: justification
}};

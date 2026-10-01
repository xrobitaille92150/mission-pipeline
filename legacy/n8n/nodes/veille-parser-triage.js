// Nœud "Parser triage Veille" (Code, runOnceForAllItems)
// (Étage de triage grossier supprimé le 27/06/2026.) Déduplique les offres par jobId
// (même offre vue dans plusieurs alertes le même jour) et formate chaque carte
// pour l'écriture Airtable. Plus d'appel Claude de pré-classement ni de champs de triage.
const src = $('Prépa triage Veille').first().json;
const cards = src.cards || [];
const toIso = (s) => { if (!s) return ''; const d = new Date(s); return isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10); };
const seen = new Set();
const out = [];
for (const c of cards) {
  if (c.jobId && seen.has(c.jobId)) continue;
  if (c.jobId) seen.add(c.jobId);
  out.push({ json: {
    jobId: c.jobId, employeur: c.employeur, poste: c.poste, lieu: c.lieu,
    mode: c.mode, url: c.url, alertName: c.alertName, date: toIso(c.date),
    easyApply: c.easyApply || false,
  }});
}
return out;

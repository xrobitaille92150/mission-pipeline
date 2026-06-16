// Nœud n8n "Parser triage Veille" (Code, runOnceForAllItems)
// Lit la réponse unique du triage (tableau JSON, un objet par offre dans l'ordre)
// et la rattache à chaque carte par index. Sort une carte par offre (jobId + pertinence).
const src = $('Prépa triage Veille').first().json;
const cards = src.cards || [];

let arr = [];
try {
  const txt = ($json && $json.content && $json.content[0] && $json.content[0].text) || '';
  const m = txt.match(/\[[\s\S]*\]/);
  if (m) arr = JSON.parse(m[0]);
} catch (e) { /* tableau vide => pertinence vide, à trier à la main */ }

const byI = {};
for (const o of arr) { if (o && o.i != null) byI[Number(o.i)] = o; }

const valid = ['Haute', 'Moyenne', 'Hors-cible'];
const toIso = (s) => { if (!s) return ''; const d = new Date(s); return isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10); };

return cards.map((c, idx) => {
  const o = byI[idx + 1] || {};
  let pertinence = o.pertinence;
  if (!valid.includes(pertinence)) pertinence = '';
  return {
    json: {
      jobId: c.jobId, employeur: c.employeur, poste: c.poste, lieu: c.lieu,
      mode: c.mode, url: c.url, alertName: c.alertName, date: toIso(c.date),
      pertinence, raison: o.raison || '',
    },
  };
});

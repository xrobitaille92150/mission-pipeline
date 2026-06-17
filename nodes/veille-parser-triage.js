// Nœud n8n "Parser triage Veille" (Code, runOnceForAllItems)
// Lit la réponse unique du triage (tableau JSON, un objet {i,pertinence,raison}
// par offre, DANS L'ORDRE) et la rattache à chaque carte par index.
// Robuste : si la réponse IA est tronquée (stop_reason=max_tokens), on récupère
// quand même TOUS les objets complets déjà reçus, au lieu de tout perdre.
// Déduplique aussi les offres répétées le même jour (même jobId apparu dans
// plusieurs alertes) pour éviter les doublons à la création.
const src = $('Prépa triage Veille').first().json;
const cards = src.cards || [];

// 1) Extraire chaque objet {...} un par un (tolérant à une fin coupée :
//    un dernier objet incomplet n'a pas de "}" -> il est simplement ignoré).
const txt = ($json && $json.content && $json.content[0] && $json.content[0].text) || '';
const byI = {};
const objs = txt.match(/\{[^{}]*\}/g) || [];
for (const frag of objs) {
  try {
    const o = JSON.parse(frag);
    if (o && o.i != null) byI[Number(o.i)] = o;
  } catch (e) { /* objet incomplet ignoré */ }
}

const valid = ['Haute', 'Moyenne', 'Hors-cible'];
const toIso = (s) => { if (!s) return ''; const d = new Date(s); return isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10); };

// 2) Une carte par offre, dédupliquée par jobId (on garde la 1ère occurrence).
//    On garde l'index d'origine (idx) pour aligner avec la réponse IA.
const seen = new Set();
const out = [];
cards.forEach((c, idx) => {
  if (c.jobId && seen.has(c.jobId)) return; // doublon intra-journée -> ignoré
  if (c.jobId) seen.add(c.jobId);
  const o = byI[idx + 1] || {};
  let pertinence = o.pertinence;
  if (!valid.includes(pertinence)) pertinence = '';
  out.push({
    json: {
      jobId: c.jobId, employeur: c.employeur, poste: c.poste, lieu: c.lieu,
      mode: c.mode, url: c.url, alertName: c.alertName, date: toIso(c.date),
      pertinence, raison: o.raison || '',
    },
  });
});
return out;

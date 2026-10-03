// Nœud n8n "Veille décider" (Code, runOnceForAllItems)
// Décide create vs update pour chaque carte, SANS dépendre du pairedItem :
// le nœud "Chercher Veille" (search Airtable) casse l'appariement 1-pour-1, donc
// on rapproche en JS via .all() (même principe que "Matcher & décider").
// Index des lignes Veille existantes (résultats du search) : jobId -> id Airtable.
const existing = {};
for (const it of $('Chercher Veille').all()) {
  const j = it.json || {};
  if (j.id && j.jobId != null) existing[String(j.jobId)] = j.id;
}

return $('Parser triage Veille').all().map((it) => {
  const c = it.json;
  const airtableId = existing[String(c.jobId)] || null;
  return { json: { ...c, action: airtableId ? 'update' : 'create', airtableId } };
});

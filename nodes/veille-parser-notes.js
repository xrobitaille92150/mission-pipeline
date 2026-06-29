const rec = $('Prépa prompt notes').item.json;
let role = '', crit = '', modeFromNotes = '';
try {
  const txt = ($json && $json.content && $json.content[0] && $json.content[0].text) || '';
  const m = txt.match(/\{[\s\S]*\}/);
  if (m) { const o = JSON.parse(m[0]); role = o.note_role || ''; crit = o.note_criteres || ''; modeFromNotes = o.mode || ''; }
} catch (e) { /* laisse vide -> retente au prochain run */ }
if (!role && !crit) return null;
return { json: { id: rec.id, noteRole: role, noteCriteres: crit, modeFromNotes, modeFromJd: rec.modeFromJd || '', easyApply: !!rec.easyApply } };
// Parse la reponse de triage Claude et reattache les champs de la carte (paire).
const card = $('Prépa triage Veille').item.json;
let pertinence='', raison='';
try {
  const txt = ($json && $json.content && $json.content[0] && $json.content[0].text) || '';
  const m = txt.match(/\{[\s\S]*\}/);
  if (m) { const o = JSON.parse(m[0]); pertinence = o.pertinence||''; raison = o.raison||''; }
} catch(e) {}
if (!['Haute','Moyenne','Hors-cible'].includes(pertinence)) pertinence='';
const toIso=(s)=>{ if(!s) return ''; const d=new Date(s); return isNaN(d.getTime())?'':d.toISOString().slice(0,10); };
return { json: { jobId:card.jobId, employeur:card.employeur, poste:card.poste, lieu:card.lieu, mode:card.mode, url:card.url, alertName:card.alertName, date:toIso(card.date), pertinence, raison } };

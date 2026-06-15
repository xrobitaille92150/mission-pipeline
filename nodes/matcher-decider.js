// Nœud n8n "Matcher & décider" (Code, runOnceForAllItems)
// Lit les résultats du batch (JSONL) + la liste Airtable, rapproche chaque
// email d'une candidature existante, et décide : create / update / skip / review.
// Propage en sortie les enrichissements LinkedIn (URL, Lieu, Mode).

const DENY = new Set(['indigoneo']);

// Hiérarchie des statuts : on n'avance que vers l'avant
const RANK = { '': 0, 'neant': 0, 'envoye': 1, 'a/r': 2, 'ar': 2, 'oui': 3, 'non': 3 };
const rankOf = (rp) => RANK[String(rp || '').trim().toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')] ?? 0;

const toIso = (s) => { if (!s) return ''; const d = new Date(s); return isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10); };

const normalize = (s) => String(s || '')
  .toLowerCase()
  .normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/\b(sas|sasu|sa|sarl|inc|ltd|llc|gmbh|group|groupe|technologies|tech|recruitment|recrutement|staffing)\b/g, '')
  .replace(/[^a-z0-9]/g, '');

const getField = (f, name) => {
  if (!f) return '';
  if (f[name] != null) return f[name];
  const norm = (s) => String(s).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  const target = norm(name);
  for (const k of Object.keys(f)) if (norm(k) === target) return f[k];
  return '';
};

const indexed = $input.all().map((i) => {
  const f = i.json.fields || i.json;
  return {
    id: getField(f, 'id') || i.json.id,
    societe: getField(f, 'societe'),
    poste: getField(f, 'poste'),
    reponse: getField(f, 'reponse'),
    key: normalize(getField(f, 'societe')),
  };
});

const findMatch = (societe, meta) => {
  const keyS = normalize(societe);
  if (keyS.length > 1) {
    const m = indexed.find(r => r.key && (r.key === keyS || r.key.includes(keyS) || keyS.includes(r.key)));
    if (m) return m;
  }
  const hay = normalize(`${meta.subject || ''} ${meta.snippet || ''} ${meta.bodyClean || ''} ${meta.fromRaw || ''}`);
  if (hay) {
    const hits = indexed
      .filter(r => r.key && r.key.length >= 4 && hay.includes(r.key))
      .sort((a, b) => b.key.length - a.key.length);
    if (hits.length) return hits[0];
  }
  const keyD = normalize((meta.domain || '').split('.')[0]);
  if (keyD.length > 2) {
    const m = indexed.find(r => r.key && (r.key.includes(keyD) || keyD.includes(r.key)));
    if (m) return m;
  }
  return null;
};

const metaById = {};
for (const it of $('Filtrer & préparer').all()) {
  metaById[it.json.customId] = it.json;
}

const r = $('Récupérer résultats').first().json;
const raw = (typeof r === 'string') ? r : (r.data || r.body || r.text || '');
const lines = String(raw).split('\n').map(l => l.trim()).filter(Boolean);

const out = [];
for (const line of lines) {
  let entry;
  try { entry = JSON.parse(line); } catch (e) { continue; }

  const meta = metaById[entry.custom_id];
  if (!meta) continue;

  let extracted = { recrutement: false, societe: null, poste: null, reponse: 'A/R', confidence: 'low', note: '' };
  if (entry.result && entry.result.type === 'succeeded') {
    try {
      const text = entry.result.message?.content?.[0]?.text || '';
      const clean = text.replace(/```json\n?/g, '').replace(/```/g, '').trim();
      extracted = { ...extracted, ...JSON.parse(clean) };
    } catch (e) { /* défaut conservé */ }
  }

  const forced = !!meta.forcedReponse;
  if (extracted.recrutement !== true && !forced) continue;

  // société + fiabilité (forcée OU extraite par Claude = fiable ; fallback domaine = non fiable)
  let societe, societeReliable;
  if (meta.forcedSociete) { societe = meta.forcedSociete; societeReliable = true; }
  else if (extracted.societe && extracted.societe !== 'null') { societe = extracted.societe; societeReliable = true; }
  else { societe = meta.domain ? meta.domain.split('.')[0] : ''; societeReliable = false; }

  // Poste : préfère l'extraction LinkedIn déterministe, sinon Claude
  const poste = meta.forcedPoste
    ? meta.forcedPoste
    : ((extracted.poste && extracted.poste !== 'null') ? extracted.poste : '');

  // Enrichissements LinkedIn (vides pour les autres emails)
  const lieu = meta.forcedLieu || '';
  const mode = meta.forcedMode || '';
  const linkedinUrl = meta.forcedUrl || '';

  const reponse = forced
    ? meta.forcedReponse
    : (['Oui', 'Non', 'A/R'].includes(extracted.reponse) ? extracted.reponse : 'A/R');

  if (DENY.has(normalize(societe))) continue;

  const match = findMatch(societe, meta);

  let action, airtableId = null;
  if (match) {
    airtableId = match.id;
    action = rankOf(reponse) > rankOf(match.reponse) ? 'update' : 'skip';
  } else if (societeReliable && normalize(societe).length >= 2) {
    action = 'create';
  } else {
    action = 'review';
  }

  out.push({
    json: {
      action, airtableId, societe, poste, reponse,
      lieu, mode, linkedinUrl,
      subject: meta.subject, fromRaw: meta.fromRaw, date: toIso(meta.date),
      note: extracted.note || '',
      gmailLink: meta.emailId ? `https://mail.google.com/mail/u/0/#all/${meta.emailId}` : '',
      domain: meta.domain, emailId: meta.emailId,
      confidence: extracted.confidence || 'low',
      forced, existingReponse: match ? match.reponse : null,
    },
  });
}

// Dédoublonnage intra-run : create/update oui ; review JAMAIS (l'upsert Airtable gère le cross-run)
const best = new Map();
const reviews = [];
for (const o of out) {
  if (o.json.action === 'review') { reviews.push(o); continue; }
  const k = (o.json.action === 'create') ? ('soc:' + normalize(o.json.societe)) : ('id:' + o.json.airtableId);
  const prev = best.get(k);
  if (!prev || rankOf(o.json.reponse) > rankOf(prev.json.reponse)) best.set(k, o);
}
return [...best.values(), ...reviews];

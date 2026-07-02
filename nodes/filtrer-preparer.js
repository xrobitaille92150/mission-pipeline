// Nœud n8n "Filtrer & préparer" (Code, runOnceForAllItems)
// Décode le corps des emails, applique les règles déterministes LinkedIn,
// extrait URL/Poste/Lieu/Mode pour les confirmations Envoyé, et construit
// le prompt Claude. Sortie : un item par email à classifier.
//
// Les alertes d'offres LinkedIn (jobalerts-noreply) ne sont plus ignorées :
// leur digest text/plain est découpé en cartes (une par offre) taguées
// kind:'veille'. Un Switch en aval ("Veille ?") sépare kind:'veille' du flux
// Candidatures (kind:'candidature'). Cf. veille-jobalerts.feature.md.

const items = $input.all();
const results = [];

const decodeB64 = (data) => {
  if (!data) return '';
  try { return Buffer.from(data.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8'); }
  catch (e) { return ''; }
};

const extractText = (parts) => {
  if (!Array.isArray(parts)) return '';
  let text = '';
  for (const part of parts) {
    if (part.mimeType === 'text/plain' && part.body?.data) text += decodeB64(part.body.data) + ' ';
    else if (Array.isArray(part.parts)) text += extractText(part.parts);
  }
  return text;
};

// Lit un en-tête depuis le dict normalisé du nœud Gmail v2 (clés minuscules,
// valeurs de la forme "Nom: valeur").
const headerFromMap = (email, name) => {
  const h = email.headers;
  if (h && typeof h === 'object' && !Array.isArray(h)) {
    const raw = h[name.toLowerCase()];
    if (raw) return String(raw).replace(new RegExp('^\\s*' + name + '\\s*:\\s*', 'i'), '').trim();
  }
  return '';
};

// Récupère un en-tête quel que soit le format de sortie du nœud Gmail :
//  - brut (get full)            : email.payload.headers = [{name, value}]
//  - normalisé (node v2, simple:false) : champ direct minuscule (email.subject / email.date / …)
//    + dict email.headers {<nom-minuscule>: "Nom: valeur"}
// ⚠️ Le nœud "Gmail — Emails de la veille" renvoie la forme NORMALISÉE (pas de payload) :
// sans ce support, subject/from/date/domain remontaient tous vides (dates jamais écrites,
// règles LinkedIn forcées et filtrage jobalerts inopérants).
const getHeader = (email, name) => {
  const headers = email.payload?.headers;
  if (Array.isArray(headers)) {
    const found = headers.find(h => (h.name || '').toLowerCase() === name.toLowerCase());
    if (found) return found.value || '';
  }
  const low = name.toLowerCase();
  if (typeof email[low] === 'string' && email[low]) return email[low];
  const fromMap = headerFromMap(email, name);
  if (fromMap) return fromMap;
  const cap = name.charAt(0).toUpperCase() + name.slice(1);
  return email[name] || email[cap] || '';
};

const cutName = (s) => s.split(/\s[-|–—:]\s|\n|\bpour\b|\bfor\b/i)[0].trim().slice(0, 60);
const escRe = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// Normalise vers les valeurs FR utilisées dans Airtable Candidatures : "À distance" / "Hybride" / "Sur site"
const cleanMode = (raw) => {
  const m = String(raw || '').toLowerCase();
  if (/(à\s*distance|\ba\s*distance\b|remote|télétravail|teletravail|full\s*remote)/.test(m)) return 'À distance';
  if (/(hybride|hybrid)/.test(m)) return 'Hybride';
  if (/(sur\s*(?:site|place)|on[-\s]?site|présentiel|presentiel)/.test(m)) return 'Sur site';
  return '';
};

// Trouve une URL d'offre LinkedIn dans un texte (HTML ou plain)
const findLinkedInJobUrl = (text) => {
  if (!text) return '';
  const m = String(text).match(/https?:\/\/(?:[\w.-]+\.)?linkedin\.com\/(?:comm\/)?jobs\/view\/(\d+)/i);
  return m ? `https://www.linkedin.com/jobs/view/${m[1]}/` : '';
};

// Cherche l'URL d'offre dans toutes les parties du payload (plain + html)
const findLinkedInJobUrlInPayload = (payload) => {
  if (!payload) return '';
  if (payload.body?.data) {
    const u = findLinkedInJobUrl(decodeB64(payload.body.data));
    if (u) return u;
  }
  if (Array.isArray(payload.parts)) {
    for (const p of payload.parts) {
      const u = findLinkedInJobUrlInPayload(p);
      if (u) return u;
    }
  }
  return '';
};

// --- Parseur de digest d'alertes emploi LinkedIn (jobalerts-noreply) ---
// Le corps text/plain liste les offres en blocs séparés par des lignes de tirets :
//   <Poste> / <Employeur> / <Lieu> / [badge…] / "Voir l'offre d'emploi : <URL …/jobs/view/JOBID/…>"
// Validé sur emails réels (2026-06-16). Pas de description du poste dans l'email
// (contrainte fiche) → on n'extrait que titre/employeur/lieu/mode + jobId/URL.
// Renvoie une carte par jobId (dédup intra-email ; dédup inter-jours = upsert Airtable).
const VEILLE_BADGES = /^(Top candidat|Croissance rapide|Recrutement actif|Cette entreprise recrute activement|Soyez parmi les premiers|Postulez avec|Candidature simplifiée|Easy Apply|Reprend contact|Forte affinité|Promu|En vedette|Actively recruiting|Be an early applicant|Top applicant|Salaire\b|\d+\s+relation)/i;

const parseJobAlertDigest = (plain) => {
  const cards = [];
  if (!plain) return cards;
  const seen = new Set();
  // Nom de l'alerte (recherche LinkedIn enregistrée) — contexte utile au triage
  const alertM = plain.match(/Votre alerte Emploi pour\s+(.+)/i);
  const alertName = alertM ? alertM[1].trim().slice(0, 200) : '';
  const blocks = plain.split(/\n[ \t]*-{3,}[ \t]*\n/);
  for (const block of blocks) {
    const jm = block.match(/linkedin\.com\/(?:comm\/)?jobs\/view\/(\d+)/i);
    if (!jm) continue;                       // en-tête / pied de page sans offre
    const jobId = jm[1];
    if (seen.has(jobId)) continue;           // dédup intra-email
    seen.add(jobId);
    const url = `https://www.linkedin.com/jobs/view/${jobId}/`;
    const rawLines = block.split('\n')
      .map(l => l.replace(/\s+/g, ' ').trim())
      .filter(Boolean);
    const easyApply = rawLines.some(l => /candidature simplifi\u00e9e|easy apply/i.test(l));
    const lines = rawLines
      .filter(l => !/^Votre alerte Emploi pour/i.test(l))
      .filter(l => !/^Voir l.offre d.emploi\s*:/i.test(l))
      .filter(l => !VEILLE_BADGES.test(l));
    const poste = (lines[0] || '').slice(0, 200);
    const employeur = (lines[1] || '').slice(0, 120);
    let lieu = (lines[2] || '').slice(0, 120);
    // Mode parfois accolé au lieu : "France (à distance)", "Paris (Hybride)"
    let mode = '';
    const lp = lieu.match(/^(.*?)\s*\(([^)]+)\)\s*$/);
    if (lp) { const m = cleanMode(lp[2]); if (m) { mode = m; lieu = lp[1].trim(); } }
    if (!mode) mode = cleanMode(lieu);
    cards.push({ jobId, url, employeur, poste, lieu, mode, alertName, easyApply });
  }
  return cards;
};

for (const item of items) {
  const email = item.json;

  const subject = getHeader(email, 'Subject');
  // From : objet normalisé {value:[{address,name}]} (node v2) OU en-tête brut.
  const fromObj = email.from;
  let fromRaw = '';
  if (fromObj && typeof fromObj === 'object' && Array.isArray(fromObj.value) && fromObj.value[0]) {
    const v = fromObj.value[0];
    fromRaw = v.name ? `${v.name} <${v.address || ''}>` : (v.address || '');
  } else {
    fromRaw = getHeader(email, 'From');
  }
  const dateRaw = getHeader(email, 'Date'); // node v2 : déjà ISO (ex. 2026-06-15T22:09:30.000Z)
  const fromEmail = (
    (fromObj && typeof fromObj === 'object' && fromObj.value && fromObj.value[0] && fromObj.value[0].address)
    || (fromRaw.match(/<([^>]+)>/) || [])[1]
    || fromRaw.trim()
    || ''
  ).toLowerCase();
  const domain = fromEmail.includes('@') ? fromEmail.split('@')[1] : '';
  const snippet = email.snippet
    || (typeof email.text === 'string' ? email.text.replace(/\s+/g, ' ').trim().slice(0, 400) : '');
  const isLinkedIn = domain.includes('linkedin.com');

  // Règles déterministes LinkedIn (sur sujet + aperçu)
  let forcedReponse = null;
  let forcedSociete = null;
  if (isLinkedIn) {
    const head = `${subject}\n${snippet}`;
    let m;
    if ((m = head.match(/your application was sent to\s+(.+)/i)) ||
        (m = head.match(/votre candidature a été envoyée à\s+(.+)/i)) ||
        (m = head.match(/candidature(?:\s+a(?:\s+bien)?\s+été)?\s+envoyée\s+à\s+(.+)/i))) {
      forcedReponse = 'Envoyé'; forcedSociete = cutName(m[1]);
    } else if ((m = head.match(/your application was viewed by\s+(.+)/i)) ||
               (m = head.match(/candidature a été consultée par\s+(.+)/i))) {
      forcedReponse = 'A/R'; forcedSociete = cutName(m[1]);
    } else if ((m = head.match(/derni[eè]re nouvelle de\s+(.+)/i))) {
      forcedReponse = 'Non'; forcedSociete = cutName(m[1]);
    }
  }

  // Alertes d'offres LinkedIn -> branche Veille (parse du digest en cartes)
  if (!forcedReponse && fromEmail === 'jobalerts-noreply@linkedin.com') {
    // Confirmations de création d'alerte : pas des offres (bug 02/07 — records poubelle)
    if (/votre alerte emploi a été créée/i.test(subject)) continue;
    let plain = '';
    if (email.payload && Array.isArray(email.payload.parts)) plain = extractText(email.payload.parts);
    else if (email.payload?.body?.data) plain = decodeB64(email.payload.body.data);
    if (!plain) plain = email.plaintextBody || email.text || '';
    for (const c of parseJobAlertDigest(plain)) {
      results.push({
        json: {
          kind: 'veille',
          jobId: c.jobId, url: c.url,
          employeur: c.employeur, poste: c.poste, lieu: c.lieu, mode: c.mode,
          alertName: c.alertName, easyApply: c.easyApply || false,
          emailId: email.id || '', date: dateRaw,
        },
      });
    }
    continue; // ne pas envoyer ces emails dans le flux Candidatures
  }

  let bodyRaw = '';
  if (email.payload) {
    if (Array.isArray(email.payload.parts)) bodyRaw = extractText(email.payload.parts);
    else if (email.payload.body?.data) bodyRaw = decodeB64(email.payload.body.data);
  }
  if (!bodyRaw) bodyRaw = email.text || email.html || snippet || '';

  let bodyClean = String(bodyRaw).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  if (bodyClean.length > 2500) bodyClean = bodyClean.slice(0, 1800) + ' […] ' + bodyClean.slice(-700);

  // Enrichissement LinkedIn (Envoyé uniquement) — URL / Poste / Lieu / Mode
  let forcedUrl = '';
  let forcedPoste = null;
  let forcedLieu = null;
  let forcedMode = null;
  if (isLinkedIn && forcedReponse === 'Envoyé' && forcedSociete) {
    forcedUrl = findLinkedInJobUrlInPayload(email.payload);

    // Pattern : "<Société> <Poste> <Société> · <Lieu> (<Mode>?) ... Candidature/Application/Voir/View"
    const escSoc = escRe(forcedSociete);
    const stop = '(?:\\s+Candidature\\s+envoyée|\\s+Application\\s+sent|\\s+View\\s+job|\\s+Voir\\s+l|\\s+Postuler|$)';
    const re = new RegExp(
      `${escSoc}\\s+([^·•]+?)\\s+${escSoc}\\s*[·•]\\s*([^·•]+?)${stop}`,
      'i'
    );
    const mm = bodyClean.match(re);
    if (mm) {
      forcedPoste = mm[1].trim().slice(0, 200);
      const meta = mm[2].trim();
      const lm = meta.match(/^(.+?)\s*(?:\(([^)]+)\))?\s*$/);
      if (lm) {
        forcedLieu = lm[1].trim().slice(0, 100);
        forcedMode = cleanMode(lm[2] || meta);
      }
    }
  }

  const customId = ('m_' + (email.id || String(results.length)))
    .replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 64);

  const claudePrompt = `Analyse cet email de candidature à l'emploi (en français) et classe la réponse de l'employeur.

Expéditeur : ${fromRaw}
Domaine expéditeur : ${domain}
Sujet : ${subject}
Corps : ${bodyClean || snippet}

Réponds UNIQUEMENT avec ce JSON, sans texte ni balises autour :
{"recrutement": true/false, "societe": "... ou null", "poste": "... ou null", "reponse": "A/R|Oui|Non", "confidence": "high|medium|low", "note": "1 phrase"}

DÉFINITIONS
- recrutement=true : email lié à une candidature précise (accusé de réception d'ATS, refus, invitation entretien, message d'un recruteur sur une candidature).
- recrutement=false : alertes/offres non sollicitées, newsletters ; emails d'AUTHENTIFICATION / code de connexion / vérification ; emails de BIENVENUE / création de compte / inscription à une plateforme ; invitations à une CONFÉRENCE / webinaire / événement (≠ entretien d'embauche) ; notifs bancaires, factures, pub, réseaux sociaux.
- societe : l'ENTREPRISE qui recrute. Si elle n'est pas explicite, DÉDUIS-LA de la signature, de l'adresse postale, du logo/nom cité dans le corps, ou du domaine de l'expéditeur. Jamais la plateforme/ATS ni le cabinet (ignorer Welcome to the Jungle, Greenhouse, Lever, Workday, LinkedIn). null seulement si vraiment introuvable.
- poste : déduis l'intitulé depuis le sujet, le corps ou la signature. null si introuvable.

CLASSEMENT DE reponse — lis le CORPS, pas seulement l'objet :
- "A/R" = accusé de réception, AUCUNE décision : « bien reçu votre candidature », « candidature enregistrée », « merci pour votre candidature », confirmation automatique.
- "Non" = refus : « ne donnerons pas suite », « profil n'a pas été retenu », « malheureusement », « regret », « unfortunately ».
- "Oui" = suite positive RÉELLE : invitation entretien/échange/appel/visio, test ou screening de sélection, « souhaitons vous rencontrer », « vos disponibilités », « prochaine étape ». PAS une invitation à une conférence/événement ni une simple inscription.

RÈGLES
- Un accusé de réception n'est PAS un refus. A/R ≠ Non ≠ Oui.
- En cas de doute entre les trois (si c'est bien du recrutement) → "A/R".`;

  results.push({
    json: {
      kind: 'candidature',
      customId, emailId: email.id || '', subject, fromRaw, fromEmail,
      date: dateRaw, domain, snippet, bodyClean, claudePrompt,
      forcedReponse, forcedSociete, forcedUrl, forcedPoste, forcedLieu, forcedMode,
    },
  });
}

return results;
// Nœud n8n "Filtrer & préparer" (Code, runOnceForAllItems)
// Décode le corps des emails, applique les règles déterministes LinkedIn,
// extrait URL/Poste/Lieu/Mode pour les confirmations Envoyé, et construit
// le prompt Claude. Sortie : un item par email à classifier.

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

const getHeader = (email, name) => {
  const headers = email.payload?.headers;
  if (Array.isArray(headers)) {
    const found = headers.find(h => (h.name || '').toLowerCase() === name.toLowerCase());
    if (found) return found.value || '';
  }
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

for (const item of items) {
  const email = item.json;

  const subject = getHeader(email, 'Subject');
  const fromRaw = getHeader(email, 'From');
  const dateRaw = getHeader(email, 'Date');
  const fromEmail = (fromRaw ? ((fromRaw.match(/<([^>]+)>/) || [])[1] || fromRaw.trim()) : '').toLowerCase();
  const domain = fromEmail.includes('@') ? fromEmail.split('@')[1] : '';
  const snippet = email.snippet || '';
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

  // Alertes d'offres LinkedIn -> mises de côté
  if (!forcedReponse && fromEmail === 'jobalerts-noreply@linkedin.com') continue;

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
      customId, emailId: email.id || '', subject, fromRaw, fromEmail,
      date: dateRaw, domain, snippet, bodyClean, claudePrompt,
      forcedReponse, forcedSociete, forcedUrl, forcedPoste, forcedLieu, forcedMode,
    },
  });
}

return results;

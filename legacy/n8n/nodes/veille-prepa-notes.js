const PROFIL = `Profil candidat : consultant senior independant, 25 ans d'experience, 100% assurance & finance. Cherche mission freelance/contract/interim (CDI senior finance possible), full remote ou hybride privilegie, TJM >= 800 EUR/j. Ouvert a l'international en remote (EMEA, Amerique du Nord, APAC).`;
const r = $json;
const prompt = `${PROFIL}

Voici une offre d'emploi. Redige DEUX notes courtes en francais, factuelles, pour aider ce candidat a decider de postuler.

OFFRE : ${r.poste} — ${r.employeur} — ${r.lieu || 'lieu n.c.'}
CRITERES LINKEDIN : ${r.criteres || 'n.c.'}
DESCRIPTION :
${(r.descText || '').slice(0, 6000)}

Reponds UNIQUEMENT avec un objet JSON, sans texte autour :
{"note_role":"3 a 5 phrases : de quoi parle le poste, missions cles, seniorite, et en quoi il matche ou non le profil sur le FOND du role","note_criteres":"3 a 5 puces separees par des retours a la ligne, chaque ligne prefixee '- ' : pour chaque critere cle du recruteur (seniorite, type de contrat, secteur, competences, langue, localisation/remote) indique match ou ecart vs le profil","mode":"valeur parmi : A distance, Hybride, Sur site — ou null si non mentionne dans l offre"}`;
return { json: { prompt, id: r.id, jobId: r.jobId, modeFromJd: r.modeFromJd || '', easyApply: !!r.easyApply } };
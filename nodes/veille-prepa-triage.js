// Nœud n8n "Prépa triage Veille" (Code, runOnceForAllItems)
// Construit UNE seule requête de triage pour TOUTES les offres du jour (évite le
// rate-limit Anthropic d'un appel par carte, et laisse l'IA comparer les offres
// entre elles). Sort un unique item { prompt, cards } consommé par l'appel Claude.

const cards = $input.all().map((it) => it.json);

const PROFIL = `Profil candidat : consultant senior independant, 25 ans d'experience, 100% assurance & finance. Cherche mission freelance/contract/interim (CDI senior finance possible), full remote ou hybride privilegie, TJM >= 1000 EUR/j. Ouvert a l'international en remote (EMEA, Amerique du Nord, APAC).`;

const RUBRIQUE = `Classe CHAQUE offre par pertinence pour ce profil, en te basant sur titre + employeur + lieu UNIQUEMENT (pas de description disponible).
- "Haute" = coeur de cible a seniorite senior : transformation finance/assurance, PMO / Program / Project Manager en finance/assurance/banque/asset management, AMOA / Business Analyst finance, Interim Finance Manager / Head of, comptabilite des investissements (IFRS 9, IFRS 17, Solvency II, fund accounting, sous-ledger titres), reporting reglementaire (ACPR, ORSA, QRT, RSR/SFCR), implementation SimCorp / Clearwater / Bloomberg / OMS, migration & apurement comptable. Un lieu en Europe/EMEA, a l'etranger ou non precise NE PENALISE PAS (remote international accepte).
- "Moyenne" = finance/PMO senior hors assurance stricte (corporate finance, banque generaliste, FP&A senior), ou coeur de cible avec un doute reel sur la seniorite.
- "Hors-cible" = junior/stage/alternance, OU metier hors finance/assurance : IT/dev pur, e-commerce, energie, marketing, RH, commercial, conseil generaliste sans ancrage finance/assurance.
REGLES : ne pas inventer d'info ; l'ABSENCE de mention "remote/teletravail" n'est PAS un signal negatif (les alertes ne le precisent jamais) ; un "Project Manager" sans secteur finance/assurance identifiable = Hors-cible ; en cas d'hesitation entre Haute et Moyenne sur une offre clairement finance/assurance senior, choisir Haute.`;

const lignes = cards
  .map((c, i) => `${i + 1}. Poste: ${c.poste || ''} | Employeur: ${c.employeur || ''} | Lieu: ${c.lieu || ''}`)
  .join('\n');

const prompt = `${PROFIL}\n\n${RUBRIQUE}\n\nReponds UNIQUEMENT avec un tableau JSON, un objet par offre, DANS L'ORDRE, sans texte autour :\n[{"i":1,"pertinence":"Haute|Moyenne|Hors-cible","raison":"<=12 mots en francais"}]\n\nOFFRES :\n${lignes}`;

return [{ json: { prompt, cards, count: cards.length } }];

// Nœud n8n "Prépa triage Veille" (Code, runOnceForAllItems)
// Construit UNE seule requête de triage pour TOUTES les offres du jour (évite le
// rate-limit Anthropic d'un appel par carte, et laisse l'IA comparer les offres
// entre elles). Sort un unique item { prompt, cards } consommé par l'appel Claude.

const cards = $input.all().map((it) => it.json);

const PROFIL = `Profil candidat : consultant senior independant, 25 ans d'experience, 100% assurance & finance. Cherche mission freelance/contract/interim (CDI senior finance possible), full remote ou hybride privilegie, TJM >= 1000 EUR/j. Langues de travail : anglais ou francais uniquement. Ouvert a l'international en remote (EMEA, Amerique du Nord, APAC).`;

const RUBRIQUE = `Classe CHAQUE offre par pertinence pour ce profil, en te basant sur titre + employeur + lieu UNIQUEMENT (pas de description disponible).

- "Haute" = deux clusters PRIORITAIRES :
  (B) Investissement/actif : investment accounting/reporting, comptabilite des placements, fund accounting, SimCorp, Clearwater, Aladdin, Bloomberg AIM, Murex, Linedata, front-to-back, middle/back office dans assurance ou asset management, migration titres.
  (C) Transformation/PMO : finance transformation, TOM, programme/project manager finance ou assurance, AMOA/Business Analyst finance, implementation lead, transformation x IA/automatisation.
  Niveau requis : senior, manager, lead, director, head of, expert.

- "Moyenne" = cluster secondaire ou perimetre adjacent :
  (A) Assurance/passif : IFRS 17, comptabilite technique, provisions techniques, reporting reglementaire (ACPR, ORSA, QRT, RSR/SFCR, Solvency II), fast close — pertinent mais moins prioritaire.
  Ou : finance/PMO senior hors assurance stricte, corporate finance, FP&A senior, doute sur la seniorite.

- "Hors-cible" = junior/stage/alternance, IT/dev pur, marketing, RH, commercial, ops non-finance, banque de detail pure, fintech early-stage, secteur public, front office/trading, wealth management B2C, OU poste exigeant allemand/neerlandais/autre langue non EN-FR.

REGLES : l'ABSENCE de mention "remote" NE PENALISE PAS (les alertes ne le precisent pas) ; un lieu Europe/EMEA non precise = acceptable ; en cas d'hesitation Haute/Moyenne sur une offre clairement finance/assurance senior, choisir Haute ; en cas d'hesitation Moyenne/Hors-cible, choisir Hors-cible (reduire le bruit).`;

const lignes = cards
  .map((c, i) => `${i + 1}. Poste: ${c.poste || ''} | Employeur: ${c.employeur || ''} | Lieu: ${c.lieu || ''}`)
  .join('\n');

const prompt = `${PROFIL}\n\n${RUBRIQUE}\n\nReponds UNIQUEMENT avec un tableau JSON, un objet par offre, DANS L'ORDRE, sans texte autour :\n[{"i":1,"pertinence":"Haute|Moyenne|Hors-cible","raison":"<=12 mots en francais"}]\n\nOFFRES :\n${lignes}`;

return [{ json: { prompt, cards, count: cards.length } }];

// Airtable → GitHub Actions : « Préparer dossier » ou « Je postule » coché ⇒ dossier dans la minute.
//
// Automation (base Mission Pipeline, table Offres) :
//   Trigger  : When record matches conditions
//              { Préparer dossier } is checked  OR  { Je postule } is checked
//              AND { Dossier le } is empty
//   Action   : Run script  (ce fichier)
//   Input variables (panneau de gauche du script) :
//     recordId  → Record ID (du trigger)
//     employeur → Employeur (du trigger)
//     poste     → Poste (du trigger)
//     token     → jeton GitHub fine-grained, permission « Contents : Read and write » sur le dépôt
//                 xrobitaille92150/mission-pipeline (le coller en clair ici, Airtable le stocke ; ne jamais le committer)
//
// Sans cette automation, rien n'est perdu : le run planifié (06:30 / 18:30) traite les cases cochées.

const { recordId, employeur, poste, token } = input.config();
const REPO = "xrobitaille92150/mission-pipeline";

const r = await fetch(`https://api.github.com/repos/${REPO}/dispatches`, {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${token}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    event_type: "dossier",
    client_payload: { record: recordId, employeur: employeur || "", poste: poste || "" },
  }),
});

if (r.status === 204) {
  console.log(`Run GitHub déclenché pour ${employeur} — ${poste}`);
} else {
  const body = await r.text();
  throw new Error(`GitHub a répondu ${r.status} : ${body.slice(0, 300)}`);
}

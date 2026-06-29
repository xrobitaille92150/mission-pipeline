// Nœud "Filtrer à enrichir" (Code, runOnceForAllItems)
// (Pré-filtre grossier supprimé le 27/06/2026 : on score TOUTES les offres.)
// On enrichit toute offre pas encore notée (Note rôle vide).
return $input.all()
  .map(it => it.json)
  .filter(r => {
    const note = r['Note rôle'];
    return !(note && String(note).trim());
  })
  .map(r => ({ json: {
    id: r.id, jobId: r.jobId, poste: r.Poste, employeur: r.Employeur,
    lieu: r.Lieu, url: r.URL,
    easyApplyFromAirtable: r['Easy Apply'] || false
  }}));

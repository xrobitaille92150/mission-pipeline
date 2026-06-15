// Nœud n8n "Construire requêtes batch" (Code, runOnceForAllItems)
// Agrège tous les emails en UNE requête Batch API Anthropic.

const items = $input.all();

const requests = items.map((item) => ({
  custom_id: item.json.customId,
  params: {
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 512,
    system: 'Tu extrais des infos depuis des emails de recrutement. Réponds UNIQUEMENT en JSON valide.',
    messages: [{ role: 'user', content: item.json.claudePrompt }],
  },
}));

// Garde-fou: un batch vide renverrait un 400 côté Anthropic.
if (requests.length === 0) {
  throw new Error('Aucun email à classifier (0 requête) — rien à envoyer au batch.');
}

return [{ json: { requests, count: requests.length } }];

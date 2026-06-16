// Decide create vs update selon la presence d'une ligne Veille avec ce jobId.
const card = $('Parser triage Veille').item.json;
const foundId = ($json && $json.id) ? $json.id : null;
return { json: { ...card, action: foundId ? 'update' : 'create', airtableId: foundId } };

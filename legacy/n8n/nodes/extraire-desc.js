const rec = $('Filtrer à enrichir').item.json;
const html = ($json && ($json.data || $json.body)) || '';
const clean = s => s
  .replace(/<[^>]+>/g, ' ')
  .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
  .replace(/&#39;|&rsquo;/g, "'").replace(/&quot;/g, '"')
  .replace(/\s+/g, ' ').trim();
let desc = '';
const i = html.indexOf('show-more-less-html__markup');
if (i >= 0) {
  let chunk = html.slice(i, i + 14000).replace(/show-more-less-html__markup[^>]*>/, '');
  const cut = chunk.search(/show-more-less__button|<\/section>/);
  if (cut > 0) chunk = chunk.slice(0, cut);
  desc = clean(chunk);
}
const crit = [];
const re = /job-criteria-subheader[^>]*>([\s\S]*?)<\/h3>[\s\S]*?job-criteria-text[^>]*>([\s\S]*?)<\/span>/g;
let m;
while ((m = re.exec(html))) crit.push(clean(m[1]) + ': ' + clean(m[2]));

// Extract mode from criteria (e.g. "Télétravail: À distance")
const cleanModeLocal = (raw) => {
  const m = String(raw || '').toLowerCase();
  if (/(à\s*distance|\ba\s*distance\b|remote|télétravail|teletravail|full\s*remote)/.test(m)) return 'À distance';
  if (/(hybride|hybrid)/.test(m)) return 'Hybride';
  if (/(sur\s*(?:site|place)|on[-\s]?site|présentiel|presentiel)/.test(m)) return 'Sur site';
  return '';
};
let modeFromJd = '';
for (const c of crit) {
  if (/télétravail|remote|workplace|travail.à.distance/i.test(c)) {
    const parts = c.split(':');
    if (parts.length > 1) modeFromJd = cleanModeLocal(parts.slice(1).join(':'));
    break;
  }
}
// Detect Easy Apply from page HTML
// Easy Apply: detect in JD HTML OR fallback on jobalert value already in Airtable
const easyApply = /candidature simplifi\u00e9e|easy apply/i.test(html) || !!rec.easyApplyFromAirtable;
return { json: { ...rec, descText: desc, criteres: crit.join(' | '), ok: desc.length > 200, modeFromJd, easyApply } };
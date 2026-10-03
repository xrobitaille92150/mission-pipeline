// Nœud n8n : Calcul du Score pour offres Veille
// Lance Claude Sonnet pour scorer chaque offre selon le barème Excel
// Entrée : offres Veille avec JD complète (title, company, location, description, etc.)
// Sortie : Score (0-100) + justification

const items = $input.all();
const results = [];

const axios = require('axios');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

// Chemin du parser barème
const parserPath = path.join(
  process.env.HOME,
  'Claude/Artifacts/mission-pipeline/scripts/parse_scoring_bareme.py'
);

async function loadBareme() {
  return new Promise((resolve, reject) => {
    const python = spawn('python3', [parserPath], {
      cwd: path.join(process.env.HOME, 'Claude/Artifacts/mission-pipeline')
    });

    let output = '';
    python.stdout.on('data', (data) => {
      output += data.toString();
    });

    python.stderr.on('data', (data) => {
      console.error('Parser error:', data.toString());
    });

    python.on('close', (code) => {
      if (code === 0) {
        try {
          resolve(JSON.parse(output));
        } catch (e) {
          reject(new Error(`Barème JSON parsing failed: ${e.message}`));
        }
      } else {
        reject(new Error(`Parser exited with code ${code}`));
      }
    });
  });
}

function formatBaremeForClaude(bareme) {
  let lines = [];
  lines.push(`# ${bareme.titre}`);
  lines.push(``);
  lines.push(`**Règles** : ${bareme.règles}`);
  lines.push(`**Seuil actionnable** : ≥ ${bareme.seuil_actionnable}/100`);
  lines.push(``);

  bareme.blocs.forEach((bloc) => {
    lines.push(`## BLOC ${bloc.numero} — ${bloc.titre}`);
    lines.push(`*Range: ${bloc.range}*`);
    lines.push(``);

    bloc.criteres.forEach((crit) => {
      lines.push(
        `- **${crit.nom}** : ${crit.points > 0 ? '+' : ''}${crit.points} pts (${crit.nature})`
      );
      if (crit.mots_cles) {
        lines.push(`  - Mots-clés : ${crit.mots_cles}`);
      }
    });
    lines.push(``);
  });

  return lines.join('\n');
}

async function scoreOffre(offre, bareme, baremeText) {
  // Charge le prompt template
  const promptPath = path.join(
    process.env.HOME,
    'Claude/Artifacts/mission-pipeline/nodes/scoring-prompt.md'
  );
  let promptTemplate = fs.readFileSync(promptPath, 'utf-8');

  // Remplace les placeholders
  promptTemplate = promptTemplate.replace('{BAREME}', baremeText);
  promptTemplate = promptTemplate.replace('{JOB_TITLE}', offre.poste || 'N/A');
  promptTemplate = promptTemplate.replace('{COMPANY}', offre.employeur || 'N/A');
  promptTemplate = promptTemplate.replace('{LOCATION}', offre.lieu || 'N/A');
  promptTemplate = promptTemplate.replace('{WORK_MODE}', offre.modalites || 'Non précisé');
  promptTemplate = promptTemplate.replace('{LANGUAGE}', 'Non spécifié');
  promptTemplate = promptTemplate.replace('{JOB_DESCRIPTION}', offre.description || 'N/A');

  // Appelle Claude Sonnet via Anthropic API
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    throw new Error('ANTHROPIC_API_KEY non définie');
  }

  const response = await axios.post('https://api.anthropic.com/v1/messages', {
    model: 'claude-sonnet-4-20250514',
    max_tokens: 2000,
    messages: [
      {
        role: 'user',
        content: promptTemplate
      }
    ]
  }, {
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01'
    }
  });

  const textContent = response.data.content.find((c) => c.type === 'text');
  if (!textContent) {
    throw new Error('No text response from Claude');
  }

  // Parse la réponse JSON
  const jsonMatch = textContent.text.match(/\{[\s\S]*\}/);
  if (!jsonMatch) {
    throw new Error('No JSON found in Claude response');
  }

  return JSON.parse(jsonMatch[0]);
}

// Exécution principale
(async () => {
  try {
    const bareme = await loadBareme();
    const baremeText = formatBaremeForClaude(bareme);

    for (const item of items) {
      const offre = item.json || item;

      // Filtre : ne scorer que les offres avec description (fetch JD réussi)
      if (!offre.description) {
        results.push({
          ...offre,
          score: null,
          score_justification: 'Description manquante (fetch JD échoué)'
        });
        continue;
      }

      const scoreResult = await scoreOffre(offre, bareme, baremeText);

      results.push({
        ...offre,
        score: scoreResult.score_final,
        score_seuil_atteint: scoreResult.seuil_atteint,
        score_justification: scoreResult.justification,
        score_resume: scoreResult.resume
      });
    }

    return results.map((r) => ({ json: r }));
  } catch (error) {
    $node.sendErrorResponse(`Erreur scoring: ${error.message}`);
  }
})();

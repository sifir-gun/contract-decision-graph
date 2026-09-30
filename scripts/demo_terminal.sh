#!/usr/bin/env bash
# Enregistrement du terminal du README (docs/images/demo-terminal.gif) : une analyse réelle
# du contrat 06 du jeu de démonstration, du lancement au verdict scellé, puis la
# vérification de la chaîne du journal d'audit. Chaque commande est affichée, puis lancée
# telle quelle.
#
# Payant : oui, environ 0,002 $ (fournisseur LLM de config/decision.yaml, clé dans .env).
# Scelle un enregistrement dans le vrai journal d'audit. Exige la base, le corpus indexé,
# les poids du modèle d'embedding (docs/exploitation.md) et jq. Le thread porte le nom du
# fichier du contrat : pour enregistrer de nouveau, CONTRACT_ID=<autre identifiant>.
#
#   asciinema rec --headless --window-size 100x44 \
#     --command "env TERM=xterm-256color bash scripts/demo_terminal.sh" demo.cast
#   agg --theme github-dark --font-size 16 --idle-time-limit 2 \
#     --last-frame-duration 10 demo.cast docs/images/demo-terminal.gif
set -euo pipefail
cd "$(dirname "$0")/.."

CONTRACT=data/contracts/demo-06-no-go-conseil.txt
ID_OPTION=${CONTRACT_ID:+ --contract-id $CONTRACT_ID}

show() {
  printf '\n\033[1;32m$\033[0m %s\n' "$1"
  sleep 1.5
  eval "$1"
}

clear
show "sed -n '/Article 5/,/Article 8/p' $CONTRACT"
TIMEFORMAT=$'\033[2m(durée réelle : %1R s)\033[0m'
show "time uv run python -m cdg.cli run $CONTRACT$ID_OPTION --operateur demo-terminal \\
    --party 'Lambda Conseil Synthétique' --party 'Mu Énergie Synthétique' \\
    | jq -f scripts/demo_terminal.jq"
show "uv run python -m cdg.cli verify"
sleep 1

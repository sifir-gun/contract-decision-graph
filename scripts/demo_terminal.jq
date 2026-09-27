# Extrait de la sortie de `cdg.cli run` montré dans l'enregistrement du README
# (scripts/demo_terminal.sh) : issue, verdicts, explication, empreintes. La sortie
# complète porte en plus le résumé du CRAG, les références retenues et l'état du thread.
{
  statut,
  decision_finale: .final_decision,
  masquage,
  verdicts: [.verdicts[] | "\(.domain) : score \(.score)"
    + (if .hard_block then ", BLOCAGE" else "" end)
    + (if .findings == [] then "" else " ; " + (.findings | join(" ; ")) end)],
  explication: [.explanation.findings[]?.text],
  synthese: .explanation.synthesis,
  decision_hash,
  chain_hash
}

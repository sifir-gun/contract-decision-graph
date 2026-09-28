# Modèle d'embedding : sources et licences

Cette image contient les poids du modèle d'embedding du projet, dans le cache de Hugging Face tel que le remplit `cdg.cli fetch-embedding-model`, et rien d'autre.

- **Modèle** : [intfloat/multilingual-e5-large](https://huggingface.co/intfloat/multilingual-e5-large), licence MIT déclarée sur sa fiche (`license: mit`) ; la fiche renvoie à [microsoft/unilm](https://github.com/microsoft/unilm) (e5), sous licence MIT, dont le texte suit.
- **Conversion ONNX** : [Qdrant/multilingual-e5-large-onnx](https://huggingface.co/Qdrant/multilingual-e5-large-onnx), licence MIT déclarée sur sa fiche (`license: mit`, depuis le 24/09/2026 ; Apache-2.0 avant).
- **Fichiers** : `config.json`, `model.onnx`, `model.onnx_data`, `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json`, figés par leurs empreintes SHA-256 (`docker/modele/empreintes.sha256` du dépôt contract-decision-graph).

Relevé le 28/09/2026 ; texte de la licence de microsoft/unilm au commit `31c5b904ca1b` :

```text
The MIT License (MIT)

Copyright (c) Microsoft Corporation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

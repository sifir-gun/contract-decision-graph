# Licences tierces de l'image de l'application : provenance

Textes copiés tels quels depuis leur source, relevés le 28/09/2026 (ADR 005, « Notices de licence »). Copiés dans l'image sous `/app/licences/` ; `tests/test_licences.py` vérifie leur provenance, et, sur l'image construite, que chaque composant redistribué porte sa licence.

## Python et les bibliothèques qu'il lie (`python-build-standalone/`)

Le Python de l'image est celui que uv installe : python-build-standalone, publication `20260924`, archive `install_only_stripped`, qui ne contient que la licence de CPython (`lib/python3.12/LICENSE.txt`). Les licences des bibliothèques liées (OpenSSL, SQLite, libffi, zlib, expat, mpdecimal, liblzma, bzip2, ncurses, libedit, libuuid, Tcl/Tk et X11, Berkeley DB) sont dans le dossier `python/licenses/` des archives complètes de la même publication, identique pour les deux architectures :

| Archive | SHA-256 (fichier `SHA256SUMS` de la publication, vérifié) |
| --- | --- |
| `cpython-3.12.14+20260924-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst` | `c88ec70ba0973eb61fcdd595c0dfd8c21e6f46151a4d2b1f9ad18c5058e490d9` |
| `cpython-3.12.14+20260924-aarch64-unknown-linux-gnu-pgo+lto-full.tar.zst` | `0707d282bf6a27fc72bd0ba769baab550a42d0d0a2b6ba3910b46985b8ab7dee` |

Source : [astral-sh/python-build-standalone, publication 20260924](https://github.com/astral-sh/python-build-standalone/releases/tag/20260924). À refaire à chaque changement de version de Python ou de uv : le test compare la publication notée ici à celle que uv installe.

## Paquets Python sans texte de licence dans leur roue (`paquets/`)

| Paquet | Version | Source | Licence |
| --- | --- | --- | --- |
| flatbuffers | 25.12.19 | [google/flatbuffers](https://github.com/google/flatbuffers/blob/v25.12.19/LICENSE), tag `v25.12.19`, commit `7e163021e59c` | Apache-2.0 |
| langsmith | 0.14.0 | [langchain-ai/langsmith-sdk](https://github.com/langchain-ai/langsmith-sdk/blob/v0.14.0/LICENSE), tag `v0.14.0`, commit `334804423e0b` | MIT |
| loguru | 0.7.3 | [Delgan/loguru](https://github.com/Delgan/loguru/blob/0.7.3/LICENSE), tag `0.7.3`, commit `ae3bfd1b85b6` | MIT |
| tokenizers | 0.23.2 | [huggingface/tokenizers](https://github.com/huggingface/tokenizers/blob/v0.23.2/LICENSE), tag `v0.23.2`, commit `88a4498ad4ea` | Apache-2.0 |

Aucun de ces dépôts n'a de fichier `NOTICE` à ce tag. Une mise à jour d'un de ces paquets fait échouer le test tant que sa ligne n'est pas revue ; un nouveau paquet sans licence dans sa roue fait échouer le test de l'image construite.

## Déjà dans l'image, sans copie

- Projet : `/app/LICENSE` (AGPL-3.0) ; HTMX : `src/cdg/adapters/web/static/htmx-LICENSE.txt` (0BSD) ; corpus : `data/corpus/SOURCES.md`.
- Paquets Debian de distroless : `/usr/share/doc/<paquet>/copyright`.
- Autres paquets Python : leur roue (`*.dist-info`, ou le dossier du paquet, comme `onnxruntime/LICENSE` et `ThirdPartyNotices.txt`).

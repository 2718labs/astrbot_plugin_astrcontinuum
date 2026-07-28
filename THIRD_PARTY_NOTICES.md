# Third-Party Notices

AstrContinuum is distributed under the GNU Affero General Public License version 3 or later.
The strict release ZIP contains the `tiktoken` encoding assets described below. The source tree
additionally vendors a CI-only validator described separately; it is not a release-ZIP member.

## OpenAI tiktoken encoding assets

AstrContinuum bundles two encoding files for deterministic, offline ordinary-text
BPE. They are sourced from OpenAI's `tiktoken` project and redistributed under
the MIT license reproduced in
`astrcontinuum/tokenization/assets/TIKTOKEN_LICENSE`.

| Asset | Official source | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `cl100k_base.tiktoken` | <https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken> | 1,681,126 | `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` |
| `o200k_base.tiktoken` | <https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken> | 3,613,922 | `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` |

The regular expressions and special-token maps follow
`tiktoken_ext.openai_public` 0.12.0 as the pinned source definition for the
offline adapter contract. The supported runtime dependency range is
`tiktoken>=0.12,<0.14`.

These two assets, their notice, and their license text are included in the strict release ZIP
allowlist.

## 2718lab AstrBot plugin validator

`scripts/vendor/2718lab_validate_plugin.py` is an unmodified snapshot of
`skills/astrbot-plugin-dev/scripts/validate_plugin.py` from 2718lab DevKit
`0.2.0+codex.20260725190515`.

- License: GNU Affero General Public License version 3 (`AGPL-3.0`), compatible
  with AstrContinuum's AGPL-3.0-or-later distribution.
- Source SHA-256:
  `57fb4d2005e631638b1b62cc4641eef809da1f79d6cb72d12cd469dbf06ec77c`.
- Vendored-file SHA-256: the same value. Release tests enforce byte-for-byte
  identity.

The vendored copy lets isolated GitHub runners execute the same mechanical
plugin checks without relying on a developer's local Codex plugin cache. It is a source-tree and
CI-only tool: neither the validator nor release tests or CI files are included in the strict
release ZIP.

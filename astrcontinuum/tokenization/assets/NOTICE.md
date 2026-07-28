# Bundled tiktoken encoding assets

AstrContinuum bundles the following OpenAI encoding files solely to construct local,
offline `tiktoken` encodings:

| Asset | Official source | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `cl100k_base.tiktoken` | <https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken> | 1,681,126 | `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` |
| `o200k_base.tiktoken` | <https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken> | 3,613,922 | `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` |

The encoding assets originate from OpenAI's `tiktoken` project. The regular
expressions and special-token maps are pinned from
`tiktoken_ext.openai_public` 0.12.0 as the source definition for
AstrContinuum's versioned offline adapter contract; `0.12.0` is not a claim
about the installed package version. The assets are redistributed under the
upstream MIT license in `TIKTOKEN_LICENSE`.

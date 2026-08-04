# 第三方声明

[English](./THIRD_PARTY_NOTICES.md) | 简体中文

> 本文件是 [English 原文](./THIRD_PARTY_NOTICES.md) 的简体中文译文，仅供说明。仓库的 `LICENSE` 文件及所列第三方许可证原文才是各许可证的规范文本；如有差异，以英文声明和原始许可证文本为准。

AstrContinuum 依照 GNU Affero General Public License 第 3 版或更高版本发布。严格发布 ZIP 包含下文所述的 `tiktoken` 编码资产。源代码树还随附了一个单独说明、仅供 CI 使用的校验器；它不是发布 ZIP 的成员。

## OpenAI tiktoken 编码资产

AstrContinuum 捆绑两个编码文件，用于确定性的离线普通文本 BPE。它们来自 OpenAI 的 `tiktoken` 项目，并依照 MIT 许可证再发布；许可证文本位于 `astrcontinuum/tokenization/assets/TIKTOKEN_LICENSE`。

| 资产 | 官方来源 | 字节数 | SHA-256 |
| --- | --- | ---: | --- |
| `cl100k_base.tiktoken` | <https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken> | 1,681,126 | `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7` |
| `o200k_base.tiktoken` | <https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken> | 3,613,922 | `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d` |

正则表达式和特殊 token 映射遵循 `tiktoken_ext.openai_public` 0.12.0；它是离线适配器契约的固定源定义。支持的运行时依赖范围为 `tiktoken>=0.12,<0.14`。

这两个资产、其声明和许可证文本均包含在严格发布 ZIP 的 allowlist 中。

## 2718lab AstrBot 插件校验器

`scripts/vendor/2718lab_validate_plugin.py` 是 2718lab DevKit `0.2.0+codex.20260725190515` 中 `skills/astrbot-plugin-dev/scripts/validate_plugin.py` 的未修改快照。

- 许可证：GNU Affero General Public License 第 3 版（`AGPL-3.0`），与 AstrContinuum 的 `AGPL-3.0-or-later` 发布方式兼容。
- 源 SHA-256：`57fb4d2005e631638b1b62cc4641eef809da1f79d6cb72d12cd469dbf06ec77c`。
- 随附文件 SHA-256：相同的值。发布测试强制要求逐字节一致。

随附副本使隔离的 GitHub runner 能够执行相同的机械插件检查，而不依赖开发者本机的 Codex 插件缓存。它仅用于源代码树和 CI：校验器、发布测试和 CI 文件均不包含在严格发布 ZIP 中。

# 贡献与分支规范

## 分支职责

- `main`：稳定分支，只接收经过验证的发布内容和紧急修复；
- `dev`：日常集成分支，允许维护者在本地验证后手动推送；较大变更仍建议通过 Pull Request 合入；
- 短期分支：从 `dev` 创建，完成后合回 `dev` 并删除；小型文档和工程维护也可以直接提交到 `dev`；
- `hotfix/*`：从 `main` 创建，修复后合入 `main`，并同步回 `dev`。

`main` 不直接提交变更，必须由 Pull Request 更新。仓库规模较小时不额外维护长期 `release/*` 分支。

## 分支命名

| 类型 | 用途 | 示例 |
| --- | --- | --- |
| `feature/*` | 新功能 | `feature/obs-chat-view` |
| `fix/*` | 普通缺陷修复 | `fix/reconnect-timeout` |
| `docs/*` | 文档变更 | `docs/architecture` |
| `refactor/*` | 不改变行为的重构 | `refactor/event-bus` |
| `test/*` | 测试相关 | `test/gift-aggregator` |
| `chore/*` | 工程和依赖维护 | `chore/update-ci` |
| `hotfix/*` | 生产紧急修复 | `hotfix/credential-leak` |

分支名使用小写英文和连字符，保持简短并表达单一目的。

## 日常流程

1. 更新本地 `dev`；
2. 从 `dev` 创建短期分支；
3. 小步提交并补充测试或文档；
4. 较大变更推送分支并向 `dev` 创建 Pull Request；小型变更可在本地验证后直接推送 `dev`；
5. 使用 Pull Request 时，等待 CI 通过并完成必要评审后合并；
6. 删除已经合并的短期分支。

功能分支应尽量短生命周期。不要在同一分支混合无关功能、格式化和大规模重构。

## 发布流程

1. 确认 `dev` 的 CI 和 MVP/版本验收通过；
2. 创建 `dev → main` Pull Request；
3. 在 PR 中记录版本内容、迁移和已知问题；
4. 合并后在 `main` 创建版本标签；
5. 如 `main` 上发生额外修复，及时同步回 `dev`。

## 合并与提交

- 功能分支合入 `dev` 推荐使用 squash merge，保持集成历史简洁；
- `dev` 合入 `main` 使用普通 merge，保留发布边界；
- 提交信息建议采用 `type: description`，例如 `feat: add OBS snapshot recovery`；
- 常用类型包括 `feat`、`fix`、`docs`、`refactor`、`test`、`chore` 和 `ci`。

## Pull Request 要求

- 描述变更目的、主要内容和验证方式；
- 一个 PR 只解决一个主题；
- 相关测试必须通过；
- 行为、协议或配置发生变化时同步更新文档；
- 重要架构选择先增加或更新 ADR；
- 不提交密钥、身份码、Cookie、用户数据和本地诊断包。

## 推荐的 GitHub 分支保护

在仓库设置中为 `main` 配置：

- 禁止直接推送，要求通过 Pull Request；
- 要求 CI 的 `Python tests` 检查通过；
- 合并前要求分支与目标分支保持最新；
- 禁止强制推送和删除分支；
- 个人开发阶段将批准数设为 0，避免无法批准自己的 PR；有其他协作者后再改为至少 1 次评审。
- 仓库创建者 `Ezio927` 作为显式用户 bypass，可在紧急情况下绕过规则；该权限不自动授予未来的 Admin 协作者。

`dev` 保持可手动推送，但建议禁止强制推送和删除；推送前必须在本地运行相关测试。

## 换行规则

- 仓库文本统一保存为 LF；
- `.bat`、`.cmd`、`.ps1` 使用 CRLF；
- `.gitattributes` 是 Git 中的最终规则，优先于个人的 `core.autocrlf`；
- `.editorconfig` 用于让编辑器在保存时遵循相同规范。

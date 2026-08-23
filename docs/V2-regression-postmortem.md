# V2 回归事故复盘（Postmortem）

> 日期：2026-08-23
> 决策：**不再沿用原 V2 开发**。当前以 `ebdeeda`（稳定 V1 baseline）为基线，之后在此之上逐步增量加功能。
> 本文件仅记录事故链与教训，作为后续开发的历史参考。

## 结论（TL;DR）

不是某一行代码写错，而是 **V2 的"数据库 schema 版本化迁移"机制本身没做对**：
列约束变更 + 表重建 + 版本记录三者**没有原子一致**，导致升级旧库时必然崩溃 / 数据损坏。
回退到 V1 是正确的决定，比继续在坏迁移上打补丁安全。

## 事故链

```
8/22 18:50  xixi1-13 推 ca7a464：V2 协议帧 + v1-v3 SQLite 迁移
   ↓ 问题：迁移重命名列但保留 NOT NULL；V2 新帧不再伪造 legacy EC、温度可空
   ↓       → 向旧库插入新帧违反 NOT NULL → 运行期崩溃
8/22 → 8/23  antonchou 推多个 fix 试图修复：
   ├─ 388b065  repair legacy raw frame migration（加 v5：重建表解除 NOT NULL）
   │    问题：v5 用「DROP 旧表 + RENAME 新表 + 重建触发器」分步执行，
   │          无跨步骤原子性 → 中途失败 = 表丢失/数据损坏
   ├─ ee79d52  isolate debug stream and sync V2 docs
   └─ e0429ab  enforce strict V2 I-V measurement pipeline
8/23 03:32  antonchou revert 回 ebdeeda（稳定 V1 baseline）
   └─ 决策：放弃 V2 迁移链路，保留 V2 历史以便日后参考
```

## 根因细节

### 1. 迁移与版本记录无原子性

`_apply_migrations`（V2 storage.py）核心循环：

```python
for version, apply in sorted(MIGRATIONS.items()):
    if version in applied:
        continue
    apply(conn)                                   # 执行迁移（含 DROP TABLE 等高危 DDL）
    conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
```

- `apply(conn)` 与「记录版本」之间没有原子提交。
- `executescript(SCHEMA)` 在迁移前已运行；v5 一旦执行 `DROP TABLE raw_frames`，后续步骤失败就没有回滚点。
- 结果：迁移中途失败 = 表处于中间态 / 数据可能丢失。

### 2. 列约束变更未考虑 SQLite 语义

- 旧 V1 库 `ec_raw` / `temperature_raw` 为 `NOT NULL`。
- V2 重命名列（`ec_raw → legacy_ec_us_cm`、`temperature_raw → temperature_raw_c`），SQLite **保留原 NOT NULL 约束**。
- 而 V2 的严格帧**不再伪造 legacy EC、允许温度为空** → 一插新数据就违反 NOT NULL → 崩溃。
- `388b065` 的 v5 本质是在补这个坑（重建表解除 NOT NULL），但补法本身高危。

### 3. 版本号与 schema 常量脱节

- `388b065` 把 `SCHEMA_VERSION` 提到 5、新增 `_migrate_v5`；
- 但 `schema_migrations` 记录的是「已应用版本」，与 `SCHEMA_VERSION` 常量、以及「新库 vs 旧库」的列状态**各自独立**。
- 不同来源的库（全新建 vs 旧库升级）走到同版本时的实际列状态不一致，迁移结果不可复现。

## 教训（V1 增量开发应遵守）

1. **数据库 schema 变更必须走事务化迁移**：一个版本一个事务；失败整体回滚，绝不出现半迁移状态。
2. **SQLite 改列约束 = 重建表，必须在同一事务内完成** DROP/CREATE/COPY/RENAME，并加行数校验。
3. **迁移必须幂等且可复现**：新库与旧库升级到同一版本后，表结构应完全一致；用测试覆盖「旧库升级」路径。
4. **不要在不理解约束继承的情况下做列重命名**：SQLite `RENAME COLUMN` 保留原约束，字段语义变化时要主动解除。
5. **迁移链路风险高时，回退比打补丁安全**：本次即此情形，revert 是正确的。
6. **引入大改动前先写旧库升级测试**：若 V2 在动 schema 前先有「v1 库 → v2 库」的自动化升级测试，NOT NULL 崩溃在 CI 就能暴露，不用等到现场。

## 当前状态

- 基线：`ebdeeda`（revert 目标，稳定 V1）
- 后端：49 个 pytest 全过；模拟数据全链路（实验启停 / 帧采集 / 历史 / 导出 / 拟合）验证通过
- 前端：typecheck 通过，Vite dev server 正常，WebSocket 实时流正常
- V2 历史 commit 保留在 git 历史中（`ca7a464`…`e0429ab`），仅作参考，不再作为开发主线

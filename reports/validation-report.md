# 验证报告

题包选用 MA-08 离散图算法，业务事件为灾害响应中心的整批应急物资夜间分拨。输入、输出、单位、范围、重复键、非有限值、原子发布和确定性均在 `instruction.md` 中公开。测试文件包含 21 个可发现的 `test_*` 行为测试，覆盖正常、边界、非法、不可行、重复键、布尔/小数类型、确定性、容量、能源、风险暴露和真实替换边界失败。

本地验证使用候选环境中的参考解路径运行：21 passed。Harbor/Docker、POSIX 快照、Oracle、NOP、目标模型和 64 条最终审核必须绑定后续冻结提交，不能沿用本候选状态。


blind red-team instruction-leakage review：已检查公开题面，不含 verifier、oracle、NOP、hidden tests、测试路径或评分绕过信息。

environment build：候选目录结构与离线 wheel 清单沿用已验证环境，正式构建待冻结快照复跑。

oracle：候选静态阶段未声明运行通过，需在冻结 POSIX 快照执行。
nop：候选静态阶段未声明运行通过，需在冻结 POSIX 快照执行。
domestic model：待在同一冻结提交上运行并保存 result 与 trajectory。
harbor cli：已定位项目内 harbor.exe，版本与 schema/help 需在发布闸门记录。
docker daemon：需以 docker info 成功为准，当前报告不把 GUI 状态当作证据。
github cli：需记录 gh --version 与 gh auth status；未提交外部仓库。
source network：领域资料来自本地专家包，外网状态不作为正确性证据。

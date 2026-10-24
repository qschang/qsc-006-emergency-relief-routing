# 应急物资夜间分拨路径计划

灾害响应中心需要把整批应急物资分配到早、晚两个发运波次，并形成可核对的车辆路径计划和资源账本。请修复 `/app/solve.py`。程序从第一个命令行参数读取 UTF-8 JSON，并将结果写入第二个参数指定的文件；成功时发布完整结果，输入无效或计算、写入失败时以非零状态退出。

输入顶层只能包含 `nodes`、`lanes`、`orders`、`wave_energy_budget`。`nodes` 是 2 至 10 个互不重复的非空字符串。

`lanes` 是 1 至 20 条记录。每条记录只能包含 `lane_id`、`from`、`to`、`early_capacity`、`late_capacity`、`open_fee`、`unit_cost`、`transit_minutes`、`exposure_minutes`、`cooling_units_per_pallet`。`lane_id` 唯一且符合 `[A-Z0-9-]{1,16}`；端点必须是不同的已声明节点；两个波次容量均为 1 至 32 的整数；`open_fee` 和 `unit_cost` 均为 0 至 1,000,000 的整数；`transit_minutes` 为 1 至 1,440 的整数；`exposure_minutes` 为 0 至 1,440 的整数；`cooling_units_per_pallet` 为 0 至 1,000 的整数。允许同一对节点间存在不同线路。风险暴露分钟和能源单位均为此任务定义的整数计量值，不代表外部实测数据。

`orders` 是 1 至 24 条记录。每条记录只能包含 `order_id`、`origin`、`destination`、`priority`、`pallets`、`sla_minutes`、`max_exposure_minutes`、`allowed_waves`。`order_id` 唯一且符合 `[A-Z0-9-]{1,16}`；起终点必须是不同的已声明节点；`priority` 为 0、1 或 2；`pallets` 为 1 至 8 的整数；`sla_minutes` 为 1 至 10,080 的整数；`max_exposure_minutes` 为 0 至 10,080 的整数；`allowed_waves` 是非空数组，只能含不重复的字符串 `early` 和/或 `late`。

`wave_energy_budget` 只能包含 `early` 和 `late` 两个键，每个值为 0 至 100,000 的整数。该值是对应波次可用的车辆能源单位上限。

一个夜班批次最多包含 24 笔订单。每张订单只能整体接收或整体拒收；接收订单须选择一个允许的波次及一条完整的有向简单路径，路径不得重复节点。路径上所有线路的 `transit_minutes` 之和不得超过该订单的 `sla_minutes`，且 `exposure_minutes` 之和不得超过其 `max_exposure_minutes`。订单的全部托盘占用所选波次路径上每条线路的对应波次容量。早、晚波次的线路容量分别核算。

每个波次的车辆能源用量，等于该波次所有接收订单逐单计算的 `pallets × 路径各线路 cooling_units_per_pallet 之和`，不得超过对应的 `wave_energy_budget`。同一线路只要在任一波次被至少一张接收订单使用，就视为夜班启用，其 `open_fee` 按整班只收一次；订单的变动费用为其托盘数乘以路径上各线路 `unit_cost` 之和。任何 JSON 对象出现重复键、缺键、多余键、错误类型、布尔数值或越界值时，输入无效。

计划须先按优先级 0、1、2 的顺序，词典序最大化各级接收订单张数；只有三项张数完全相同时，才最小化启用线路开线费与订单变动费之和。仍并列时，将订单按 `order_id` 升序排列，逐单比较 `lane_id` 路径序列（ASCII 字典序）；未接收订单的路径按空序列比较。若路径序列完全相同但波次不同，则 `early` 排在 `late` 前。选择整体词典序最小的计划。

输出只能包含 `served_counts`、`total_cost`、`orders`、`lane_loads`、`wave_energy_loads`。`served_counts` 必须含字符串键 `0`、`1`、`2` 及对应接收张数；`total_cost` 为整数。`orders` 按输入顺序给出对象 `{order_id, accepted, wave, path}`；`accepted` 为布尔值，接收时 `wave` 为 `early` 或 `late` 且 `path` 为线路 ID 数组，拒收时 `wave` 为 `null` 且 `path` 为空数组。`lane_loads` 按输入线路顺序给出对象 `{lane_id, early_load, late_load, opened}`，两个 load 分别为各波次占用托盘数，`opened` 与该线路是否在任一波次被使用一致。`wave_energy_loads` 只能包含 `early`、`late` 两个键，分别记录对应波次的车辆能源用量。合法但无订单可承运时，应成功输出全拒收的零费用计划和两个波次的零能源用量。相同输入必须产生相同计划。

拒收或发布失败不得创建或改动目标文件，也不得留下临时产物。验证器提供 Python 运行时和预装测试依赖；候选程序的输入与输出路径由命令行参数指定。

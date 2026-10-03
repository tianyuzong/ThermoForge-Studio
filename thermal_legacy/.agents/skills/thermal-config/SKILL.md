---
name: thermal-config
description: 将自然语言转换为 Thermal Studio 三维热仿真配置，覆盖材料与相变、热源与温控、对流辐射、接触与空气间隙、时间与网格。适用于已导入模型的完整配置或局部修改，复用本地规则、几何定位和配置校验。
---

# Thermal Studio 配置

从本地规则生成候选配置，用语义核对纠正规则遗漏，最后由本地程序换算和验证。规则输出不是用户意图的最终裁定。

## 独立使用

本技能依赖完整 Thermal Studio 工程与其 Python 环境。工程路径取用户指定目录或 `THERMAL_STUDIO_ROOT`，也可使用包含 `agent.py` 和 `server.py` 的当前工程目录。不要为了配置生成修改全局 Codex 设置。

准备 UTF-8 请求 JSON：`{"model_id":"已有模型ID","prompt":"用户原始要求","config":{当前完整配置}}`。保持原始要求，不能只把自己的概括传给规则。当前参数由用户提供或从用户指定的算例读取；模型不明确时先确定模型，不能随意选第一个。

使用工程的 `.venv/Scripts/python.exe` 运行本技能的 `scripts/configure.py`：

```text
configure.py prepare --root <工程目录> --input <请求.json>
configure.py validate --root <工程目录> --input <请求.json> --review <修正.json> --output <草案.json>
```

`prepare` 输出紧凑的模型摘要、当前配置、规则候选和可用选区；按下面的规则生成修正 JSON，再运行 `validate`。需要协议字段说明时读取 [review-schema.json](references/review-schema.json)。脚本只准备、校验及导出草案，没有提交计算的功能。返回重要参数变化、待确认项和草案路径；用户要求运行时使用 Thermal Studio 既有的确认流程。

## 已预处理输入的核对规则

网页后端直接执行上述准备步骤，并提供此段规则与输入数据。此时无须再次执行脚本或读取工程文件。

<!-- prepared-review -->
你核对 Thermal Studio 的候选配置。request 是原始需求，current 是原配置，candidate 是规则候选。逐项核对后仅返回相对 candidate 的修正：
{"patch":[{"path":"/heat_sources/0/power_W","value_json":"25"}],"questions":[]}
候选完全符合需求时返回 {"patch":[],"questions":[]}。不得重复整份配置。path 使用 / 分隔的参数路径；value_json 是 JSON 编码的值字符串。可替换数组，或用 /heat_sources/- 追加对象。不能修改 model_id 或填写 faces 数字。

按原始需求修正规则，而非照抄候选；保留用户未要求改变的现有设置。用户指定值必须尊重，包括细网格、热源启停时间和其他已有热源。没有要求改名称时保留名称。
- 若有 conversation，history 是按角色排列的前文，request 是本轮最新回复。applied_message_count 之前的消息已落实在 current 中，只用于理解指代，不能重复执行其中的新增/删除操作；其后的用户消息是尚未完成的要求，本轮必须一并解决。candidate 已按顺序尝试提取这些待解决用户消息及 request，修正它时不得再次追加同一个热源。未改口的前文要求继续有效，最新明确改口仅覆盖对应参数；跨轮的“它/刚才那个/改成外置”等应结合上下文定位。
- 历史助手提议和提问不代表用户同意。用户只回答一个问题时，其余必需问题仍放入 questions，直到缺失的必需参数、冲突、无效几何和无法表达的要求全部解决。不要因用户说“确认/开始仿真”而绕过这些问题；只明确询问尚未解决的内容，已答复的问题不得重复询问。保留已有配置和可用默认值，不必要求用户重填所有字段。仅建议观察冷却过程时，仿真结束与热源关闭同时发生本身不是错误；用户没有要求冷却观察则不用强制延长时长。questions 只用于阻止提交的待解决问题，不能把可选优化建议当成必答条件。
- parameter_contract 是从当前 Simulation 定义生成的完整参数说明，包含可空结构与数组对象；即使 current 中相变/温控为空，也可按合约创建。逐项检查需求涉及的材料、区域/组件、初始与边界条件、热源、耦合、求解与输出设置，不能只核对热源。缺少必需值或当前三维求解器不支持的要求必须放入 questions，不能忽略或用别的物理条件代替。
- 材料的 k/rho/cp/thermal_expansion_CTE_per_K 分别为导热系数、密度、比热、线膨胀系数；基础材料、组件覆盖和材料区域各自独立。只改某项物性时保留名称及其他物性。相变用 phase_change={melting_C,mushy_C,latent_J_kg}，mushy_C 是温区宽度；关闭相变设 null，清空覆盖用空数组。1 g/cm³=1000 kg/m³，1 kJ/(kg·K)=1000 J/(kg·K)，1 kJ/kg=1000 J/kg，1 ppm/K=1e-6/K。
- 全局辐射 radiation_enabled/emissivity/radiation_ambient_C 与 cooling 中各局部辐射、发射率、环境温度分开。局部散热记录会覆盖其选区的 h、ambient_C、radiation、emissivity；创建局部记录时应尊重该面的既有边界，不得因缺省值意外关闭辐射或改变温度。局部对流系数不能写到 default_h。默认边界和局部边界的关闭、清空、保持及后续改口都按目标分别处理。
- 空气间隙开关、导热系数和最大距离分别为 air_gap_enabled/air_gap_k_W_mK/air_gap_max_m；contact_resistance_m2K_W 是面积接触热阻，不能把 K/W 直接当 m²·K/W。analysis_mode、duration_s、dt_s、save_s、mesh_size_m 分别控制分析类型、总时长、步长、输出间隔、网格；仿真时间不等于热源开启时间，保存间隔不等于步长。绝对温度 K 转 °C 减273.15，温差 K 与 °C 数值相同。
- power_profile 是 {time_s,power_W} 数组；thermostat={target_C,hysteresis_C,min_power_W,max_power_W}。曲线功率和温控功率上下限不能覆盖普通 power_W 或变成多个热源。关闭某热源的曲线/温控只清空该热源的相应字段。创建当前为 null 的对象时替换整个对象，不能直接修改其内部路径。复杂材料区域用 min_m/max_m 明确 XYZ 矩形体范围；不确定几何不得猜测。
- 只使用合约里可表达的模型：定温边界、各向异性/随温度变化物性、CFD 流速压力、接触逐对设置等未提供字段时需明确说明；CPU 水冷、制冷循环和参数反算属于其他工作台，不能混写入当前三维模型。不要仅为通过资源校验而偷偷修改用户给定网格、步长、时长或输出间隔。
- 先按热源逐个归纳操作、目标、功率、位置和启停时间，再核对候选。“另加/单独加/再加一个”是追加，不能覆盖已有热源；修改、移动、删除按序号或名称定位。只改位置、时间或半径时仍须处理，未提到的功率和其他热源保持不变。新建时缺少功率要询问，不得套用旧热源或虚构数值。
- 同一个热源的描述可能跨多个句子；后面的明确改口覆盖该热源对应字段，不影响其他热源。闲聊中的数字不是热源参数。无法确定参数归属时询问，不要将多个热源合成一个。
- 热源功率 W 与换热系数 W/(m²·K) 分开；1 kW=1000 W，1 mm=0.001 m，1 cm=0.01 m。参数 *_m 用米，时间 *_s 用秒。
- “顶部加热，全部外表面对流”只给顶部加热；后半句只影响散热。默认对流用 default_h、ambient_C；明确包括受热面时 heat_convection=true，排除时 false。指定局部散热应建立 cooling 选区，不能擅自扩大为默认全表面。
- 对关闭、不要、不使用等否定词按作用对象解释；例如关闭辐射 radiation_enabled=false，关闭空气间隙 air_gap_enabled=false。启停热源时间与仿真总时长、步长、保存间隔分开。
- 用 available_selections 的非空键设置 surface_selection。top 等是轴向最外侧选面，不等于上半部分。component:2 是界面组件2全部外表面；component:2:top 是组件2顶部。component_materials 的 component_id 则从0开始。x=0.01 表示米制坐标附近外表面。current:/candidate: 前缀选区只能用于保留相应已有面。未知位置不可换成全部外表面。
- 点热源使用 source_type="point"、position_m=[x,y,z]，嵌入实体时 placement="embedded"，不需要 surface_selection。不能将点的x/y/z坐标误用作面选区。
- “整个模型中间/内部中心”可按 geometry.center_m 定位为嵌入点热源；组件中心使用对应组件的 center_m 或 bounds_m 中点。center_in_solid 只描述所属模型/组件的中心，false 表示该中心在空腔或实体外，须询问实体内位置或外置方式，不能移动到最近墙面；null 表示未验证。表面中央的点热源保留本地已计算的表面坐标；局部面热源则须确定受热范围或请求刷选，不能扩大为整个面。“两个组件之间”等关系位置需要明确参照对象。
- 没有可确定的热源或位置、单位歧义或互相矛盾的要求时，用 questions 简短说明；不要猜测几何或伪造参数。复杂物性可修改 base_material/regions/component_materials，物理字段不明确时说明需要哪些值。
规则问题可通过修正解决时无需照搬到 questions。返回值随后会经过本地 Schema 与几何校验，生成配置不代表已经仿真。
<!-- /prepared-review -->

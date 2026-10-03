# 工况与结果

工作台 `/openapi.json` 是工况和查询的唯一类型定义，调用 `studio_schemas` 获取。以下仅为内置软件验证例，不能套用到任意用户模型。

创建新项目，再 `studio_demo(project_id, "bar")`。读取 context，核对该模型 −X 和 +X 端面以及教学材料。以下工况使用 1000 N 轴向拉力、6 mm 网格；输入为米、牛顿、帕：

```json
{
  "schema_version": 2,
  "name": "教学拉杆 1000 N",
  "material": {
    "name": "演示钢材（假设值）",
    "young_modulus_Pa": 210000000000,
    "poisson_ratio": 0.3,
    "density_kg_m3": 7850,
    "yield_strength_Pa": 235000000,
    "source": "内置教学/软件验证假设，不代表实际材料认证"
  },
  "supports": [{"name": "左端固定", "selection": {"kind": "direction", "direction": "-x"}}],
  "loads": [{"name": "轴向拉力", "kind": "force", "selection": {"kind": "direction", "direction": "+x"}, "vector": [1000, 0, 0]}],
  "mesh": {"size_m": 0.006},
  "result_requests": [{"name": "端面轴心位移", "kind": "probe", "field": "displacement_x", "point_m": [0.1, 0.005, 0.005]}]
}
```

使用示例前必须核对生成模型的实际坐标范围。不要复制未知模型的 node_id、body_id 或 face_ids。`direction` 是单次求解前的面选择，跨模型也须校验返回 mappings。

修改载荷时深复制已保存工况，仅修改用户指定的唯一载荷，保留重力、其他载荷、连接、材料、约束和查询。每个变体使用新 request_id；重试同一个变体复用原 id。分别提交后用 `studio_compare(project_id)` 查询对比；数值差异须按相同模型与物理配置解释。

网格收敛设置 `mesh.convergence=true`，使用基准尺寸及宿主既有三档策略。用户指定应力评价区时写入 `mesh.evaluation_selection`；不以自动改变评价区的方式宣称应力收敛。

点位移 query 示例：`{"name":"测点沿-Z位移","kind":"probe","field":"displacement_z","point_m":[0.1,0.005,0.005]}`。这里 value 为全局 Z 分量；沿 -Z 的投影取该分量的相反数，并标注所做换算。点坐标必须位于实际网格，结果为无定义时说明原因。

路径 query 示例：`{"name":"轴线路径","kind":"path","field":"stress","point_m":[0.02,0.005,0.005],"end_m":[0.08,0.005,0.005],"samples":31}`。先从结果目录确认 field。应力路径是后处理插值，不作为原始积分点峰值。

反力矩 query 使用 `kind="reactions"` 以及 `moment_origin_m`，在解释和报告中明确参考点。刚度 query 必须指定唯一 `load_name`、测点和 `direction`；零载荷/位移返回无定义。

新查询不会回写历史报告。需要让探针或路径随报告交付时，在求解前加入 `result_requests`；后续查询则单独给出结果，并保留其作业编号。
